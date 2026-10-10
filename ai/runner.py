"""분석 runner: 분석 결과가 없는 프로젝트를 찾아 분석하고 백엔드에 기록한다.

백엔드는 ZIP을 저장만 하고 분석을 시작하지 않는다(back/API.md). 그 사이를 이 프로그램이 채운다.
인프라 worker(infra/worker)와 같은 방식이다: 표준 라이브러리만 쓰고, 백엔드와 같은 PC에서 돌며, 주기적으로 점검한다.

    python ai/runner.py           # 계속 돌며 5초마다 점검
    python ai/runner.py --once    # 한 번만 점검

| 설정 | 기본값 |
|---|---|
| PLATFORM_API_URL / --api-url | http://127.0.0.1:8000 |
| WORKER_API_TOKEN             | 백엔드가 소스를 S3에 저장할 때만. 로컬에 ZIP이 없으면 GET /api/worker/projects/{id}/source 로 받음 |
| UPLOAD_DIR / --upload-dir    | back/data/uploads (백엔드와 같은 변수·기본값) |
| OLLAMA_API_KEY               | 환경 변수 또는 ai/.env. 있으면 Ollama 클라우드를 씀(기본) |
| OLLAMA_BASE_URL              | https://ollama.com |
| ANTHROPIC_API_KEY            | Ollama 키가 없을 때, 또는 PAVED_AI_PROVIDER=anthropic 일 때 |
| ANTHROPIC_WORKSPACE_ID       | 키가 워크스페이스에 묶여 있지 않을 때만 (anthropic-workspace-id 헤더) |
| PAVED_AI_PROVIDER            | ollama / anthropic. 비우면 있는 키로 고름(Ollama 먼저). 둘 다 없으면 규칙 분석만 |
| PAVED_AI_MODEL / --model     | Ollama glm-5.3, Anthropic claude-opus-5-5 |
| PAVED_AI_CACHE / _CACHE_DIR  | LLM 답 캐시. 기본 켬, ~/.cache/paved-ai/llm (저장소 밖). off 로 끔 |
| --no-llm                     | LLM을 부르지 않음 |

백엔드로 옮길 때는 이 파일을 버리고 paved_ai 를 직접 부른다 (paved_ai 에는 HTTP 코드가 없음).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paved_ai.analysis import SCHEMA_VERSION, analyze_files  # noqa: E402
from paved_ai.llm import DEFAULT_MODEL, LlmUnavailable, fill_unresolved  # noqa: E402
from paved_ai.ollama import DEFAULT_BASE_URL as OLLAMA_BASE_URL  # noqa: E402
from paved_ai.ollama import DEFAULT_MODEL as OLLAMA_MODEL  # noqa: E402
from paved_ai.ollama import OllamaClient  # noqa: E402
from paved_ai.masking import backend_unsafe_paths  # noqa: E402
from paved_ai.source import SourceError, files_from_zip  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "back" / "data" / "uploads"
DOTENV = Path(__file__).resolve().parent / ".env"
REQUEST_TIMEOUT = 20
MAX_FAILURES = 3  # 같은 프로젝트가 이만큼 실패하면 멈춤
RETRY_AFTER = 120  # 실패한 프로젝트는 2분 뒤에 다시
MAX_PAGES = 10

log = logging.getLogger("ai-runner")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class RunnerError(Exception):
    """분석 자체가 안 되는 경우. permanent면 다시 시도해도 같으므로 멈춤"""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


# 소스 다운로드 상한. 백엔드 업로드 한도(200 MiB)보다 조금 크게
MAX_SOURCE_BYTES = 256 * 1024 * 1024


class Api:
    def __init__(self, base_url: str, timeout: int = REQUEST_TIMEOUT, worker_token: Optional[str] = None):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # 소스 다운로드(worker 전용 API)에만 씀. repr·로그에 남기지 않음
        self._worker_token = worker_token or None

    @property
    def can_download(self) -> bool:
        return self._worker_token is not None

    def download_source(self, project_id: str) -> bytes:
        """GET /api/worker/projects/{id}/source. 백엔드가 S3에 저장해도 API가 읽어 줌 (back/API.md)"""
        req = urllib.request.Request(f"{self.base}/api/worker/projects/{urllib.parse.quote(project_id)}/source")
        req.add_header("X-Worker-Token", self._worker_token or "")
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as res:
                data = res.read(MAX_SOURCE_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, "소스를 내려받지 못했습니다" + (" (worker 토큰 확인)" if e.code in (401, 403) else "")) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ApiError(0, f"백엔드에 연결하지 못했습니다: {getattr(e, 'reason', e)}") from None
        if len(data) > MAX_SOURCE_BYTES:
            raise RunnerError("소스 ZIP이 너무 큽니다.", permanent=True)
        return data

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                text = res.read().decode("utf-8")
                return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            # 백엔드 오류 형식 {"error": "설명"}
            try:
                message = json.loads(e.read().decode("utf-8")).get("error") or e.reason
            except (ValueError, AttributeError):
                message = str(e.reason)
            raise ApiError(e.code, str(message)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ApiError(0, f"백엔드에 연결하지 못했습니다: {getattr(e, 'reason', e)}") from None

    def projects(self) -> Iterator[dict]:
        cursor = None
        for _ in range(MAX_PAGES):
            q = {"limit": "100", **({"cursor": cursor} if cursor else {})}
            page = self._request("GET", "/api/projects?" + urllib.parse.urlencode(q))
            yield from page.get("items", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def latest_analysis(self, project_id: str) -> Optional[dict]:
        try:
            return self._request("GET", f"/api/projects/{project_id}/analyses/latest")
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    def record_analysis(self, project_id: str, source_sha256: str, result: dict) -> dict:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/analyses",
            {"schema_version": SCHEMA_VERSION, "source_sha256": source_sha256, "result": result},
        )


@dataclass
class State:
    done: Set[str] = field(default_factory=set)  # 분석이 있는 프로젝트 (다시 확인하지 않음)
    stopped: Dict[str, str] = field(default_factory=dict)  # 멈춘 프로젝트 → 이유
    failures: Dict[str, int] = field(default_factory=dict)
    retry_at: Dict[str, float] = field(default_factory=dict)


# 인프라 worker(cost.recommend)가 받는 값. 프런트 첫 화면의 선택지와 같음
EXPECTED_USERS = {"~100", "~1,000", "~10,000", "10,000+"}
TRAFFIC_PATTERNS = {"steady", "peak", "unknown"}


def scale_from_project(project: dict) -> Optional[dict]:
    """사용자가 입력한 사용 규모·예산을 그대로 넘김 (LLM이 추측하거나 바꾸지 않음).

    백엔드가 project.scale 객체로 주든 최상위 필드로 주든 받는다. 형식이 틀린 값은 버림(추측해서 고치지 않음).
    하나도 없으면 None → worker는 권장 단계로 시작하고 예산 검사는 하지 않음.
    """
    src = project.get("scale") if isinstance(project.get("scale"), dict) else project
    scale: dict = {}
    if src.get("expected_users") in EXPECTED_USERS:
        scale["expected_users"] = src["expected_users"]
    if src.get("traffic_pattern") in TRAFFIC_PATTERNS:
        scale["traffic_pattern"] = src["traffic_pattern"]
    budget = src.get("monthly_budget_usd")
    try:
        if budget is not None and not isinstance(budget, bool) and float(budget) > 0:
            scale["monthly_budget_usd"] = float(budget)
    except (TypeError, ValueError):
        pass
    return scale or None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_dotenv(path: Path = DOTENV) -> None:
    """ai/.env 의 KEY=값을 환경 변수로. 이미 있는 환경 변수는 덮어쓰지 않고, 값은 어디에도 출력하지 않음"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def make_llm_client(enabled: bool = True):
    """(client, 기본 모델). 쓸 수 있는 키가 없으면 (None, None) → 규칙 분석만"""
    if not enabled:
        log.info("LLM을 쓰지 않습니다 (--no-llm). 규칙 분석만 합니다.")
        return None, None
    provider = os.environ.get("PAVED_AI_PROVIDER", "").strip().lower()
    if provider not in ("", "ollama", "anthropic"):
        log.warning("PAVED_AI_PROVIDER=%s 는 모르는 값이라 규칙 분석만 합니다 (ollama 또는 anthropic).", provider[:20])
        return None, None
    if provider == "ollama" or (not provider and os.environ.get("OLLAMA_API_KEY")):
        key = os.environ.get("OLLAMA_API_KEY")
        if not key:
            log.info("OLLAMA_API_KEY가 없어 규칙 분석만 합니다 (ai/.env 또는 환경 변수).")
            return None, None
        return OllamaClient(key, base_url=os.environ.get("OLLAMA_BASE_URL") or OLLAMA_BASE_URL), OLLAMA_MODEL
    client = _make_anthropic()
    return (client, DEFAULT_MODEL) if client else (None, None)


def _make_anthropic():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("LLM 키(OLLAMA_API_KEY, ANTHROPIC_API_KEY)가 없어 규칙 분석만 합니다 (ai/.env 또는 환경 변수).")
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK가 없어 규칙 분석만 합니다: pip install -r ai/requirements.txt")
        return None
    # 워크스페이스에 묶이지 않은 키는 요청마다 워크스페이스 ID가 필요함 (없으면 400)
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.Anthropic(timeout=120.0, max_retries=2, default_headers=headers)


def analyze_project(
    project: dict, upload_dir: Path, llm=None, model: str = DEFAULT_MODEL, api: Optional["Api"] = None
) -> Tuple[dict, str]:
    """업로드된 ZIP을 읽어 분석. 백엔드가 기록한 지문과 같은 파일일 때만 분석한다.
    로컬 업로드 폴더에 없으면(백엔드가 S3에 저장) worker 토큰으로 백엔드의 소스 다운로드 API에서 받는다.
    llm(OllamaClient 또는 Anthropic client)이 있으면 규칙이 못 찾은 값만 LLM에 묻는다. LLM이 실패해도 규칙 결과는 기록한다."""
    path = upload_dir / f"{project['id']}.zip"
    if path.is_file():
        data = path.read_bytes()
    elif api is not None and api.can_download:
        data = api.download_source(project["id"])
    else:
        raise RunnerError(
            f"소스 ZIP이 없습니다({path}). 백엔드와 같은 PC에서 UPLOAD_DIR을 맞추거나, "
            "백엔드가 S3에 저장하면 WORKER_API_TOKEN을 설정해 다운로드 API로 받게 해 주세요.")
    digest = hashlib.sha256(data).hexdigest()
    if digest != project.get("source_sha256"):
        raise RunnerError("소스 ZIP의 지문(SHA-256)이 프로젝트에 기록된 값과 다릅니다. 분석하지 않습니다.", permanent=True)
    try:
        files = files_from_zip(data)
    except SourceError as e:
        raise RunnerError(str(e), permanent=True) from None

    analysis = analyze_files(files)
    if llm is not None and analysis.unresolved:
        try:
            outcome = fill_unresolved(analysis, files, llm, model)
            if outcome.asked:
                log.info(
                    "프로젝트 %s LLM 보조: 채움 %s, 버림 %s (모델 %s, 요청 %s, 토큰 입력 %s·출력 %s, %sms%s)",
                    project["id"], outcome.filled or "-", list(outcome.rejected) or "-", outcome.model, outcome.request_id,
                    outcome.input_tokens, outcome.output_tokens, outcome.elapsed_ms, ", 캐시" if outcome.cached else "",
                )
        except LlmUnavailable as e:
            log.warning("프로젝트 %s LLM 보조를 건너뜁니다: %s", project["id"], e)
            analysis.info("AI 보조 분석을 하지 못했습니다", f"{e} 규칙으로 찾은 값만 기록합니다.")
    result = analysis.to_result(scale=scale_from_project(project))
    unsafe = backend_unsafe_paths(result)
    if unsafe:
        # 가리기에서 놓친 것. 기록하지 않음
        raise RunnerError(f"비밀값으로 보이는 내용이 결과에 남아 기록하지 않습니다: {', '.join(unsafe)}", permanent=True)
    return result, digest


def _fail(state: State, pid: str, reason: str, permanent: bool, now: float) -> None:
    state.failures[pid] = state.failures.get(pid, 0) + 1
    if permanent or state.failures[pid] >= MAX_FAILURES:
        state.stopped[pid] = reason
        log.error("프로젝트 %s 분석을 멈춥니다: %s", pid, reason)
    else:
        state.retry_at[pid] = now + RETRY_AFTER
        log.warning("프로젝트 %s 분석 실패(%d/%d), %d초 뒤 다시: %s", pid, state.failures[pid], MAX_FAILURES, RETRY_AFTER, reason)


def run_once(
    api: Api, upload_dir: Path, state: State, clock: Callable[[], float] = time.time, llm=None, model: str = DEFAULT_MODEL
) -> Dict[str, int]:
    """한 번 점검. 기록·건너뜀·실패 개수를 돌려줌"""
    counts = {"recorded": 0, "skipped": 0, "failed": 0}
    try:
        projects = list(api.projects())
    except ApiError as e:
        log.warning("프로젝트 목록을 읽지 못했습니다: %s", e.message)
        return counts

    for project in projects:
        pid = project["id"]
        now = clock()
        if pid in state.done or pid in state.stopped or state.retry_at.get(pid, 0) > now:
            counts["skipped"] += 1
            continue
        try:
            if api.latest_analysis(pid) is not None:
                state.done.add(pid)
                counts["skipped"] += 1
                continue
            result, digest = analyze_project(project, upload_dir, llm, model, api)
            api.record_analysis(pid, digest, result)
        except RunnerError as e:
            counts["failed"] += 1
            _fail(state, pid, str(e), e.permanent, now)
            continue
        except ApiError as e:
            counts["failed"] += 1
            if e.status in (0, 429) or e.status >= 500:
                _fail(state, pid, e.message, permanent=False, now=now)
            else:
                # 404(프로젝트 삭제), 409(ZIP 변경), 422(형식) 등은 다시 보내도 같음
                _fail(state, pid, f"{e.status} {e.message}", permanent=True, now=now)
            continue

        state.done.add(pid)
        counts["recorded"] += 1
        log.info(
            "프로젝트 %s(%s) 분석 기록: 지원 %s, 못 찾은 값 %s",
            pid, project.get("name", ""), "예" if result["supported"] else "아니오", ", ".join(result["unresolved"]) or "없음",
        )
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="분석 결과가 없는 프로젝트를 분석해 백엔드에 기록")
    parser.add_argument("--api-url", default=os.getenv("PLATFORM_API_URL", DEFAULT_API))
    parser.add_argument("--upload-dir", type=Path, default=Path(os.getenv("UPLOAD_DIR", DEFAULT_UPLOAD_DIR)))
    parser.add_argument("--poll", type=float, default=5.0, help="점검 간격(초)")
    parser.add_argument("--once", action="store_true", help="한 번만 점검")
    parser.add_argument("--model", default=None, help="비우면 PAVED_AI_MODEL, 그다음 고른 LLM의 기본 모델")
    parser.add_argument("--no-llm", action="store_true", help="LLM을 부르지 않고 규칙 분석만")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    llm, default_model = make_llm_client(enabled=not args.no_llm)
    args.model = args.model or os.getenv("PAVED_AI_MODEL") or default_model or DEFAULT_MODEL
    # 백엔드가 소스를 S3에 저장하면 worker 다운로드 API로 받음 (토큰 값은 로그에 남기지 않음)
    api, state = Api(args.api_url, worker_token=os.environ.get("WORKER_API_TOKEN")), State()
    upload_dir = args.upload_dir.expanduser().resolve()
    log.info("시작: 백엔드 %s, 업로드 폴더 %s, LLM %s", api.base, upload_dir, args.model if llm else "끔")

    try:
        while True:
            counts = run_once(api, upload_dir, state, llm=llm, model=args.model)
            if args.once:
                log.info("한 번 점검 끝: 기록 %d, 건너뜀 %d, 실패 %d", counts["recorded"], counts["skipped"], counts["failed"])
                return 0 if counts["failed"] == 0 else 1
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info("종료")
        return 0


if __name__ == "__main__":
    sys.exit(main())
