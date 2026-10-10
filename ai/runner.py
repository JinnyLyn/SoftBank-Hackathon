"""분석 runner: 분석 결과가 없는 프로젝트를 찾아 분석하고 백엔드에 기록한다.

백엔드는 ZIP을 저장만 하고 분석을 시작하지 않는다(back/API.md). 그 사이를 이 프로그램이 채운다.
인프라 worker(infra/worker)와 같은 방식이다: 표준 라이브러리만 쓰고, 백엔드와 같은 PC에서 돌며, 주기적으로 점검한다.

    python ai/runner.py           # 계속 돌며 5초마다 점검
    python ai/runner.py --once    # 한 번만 점검

| 설정 | 기본값 |
|---|---|
| PLATFORM_API_URL / --api-url | http://127.0.0.1:8000 |
| UPLOAD_DIR / --upload-dir    | back/data/uploads (백엔드와 같은 변수·기본값) |

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
from paved_ai.masking import backend_unsafe_paths  # noqa: E402
from paved_ai.source import SourceError, files_from_zip  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "back" / "data" / "uploads"
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


class Api:
    def __init__(self, base_url: str, timeout: int = REQUEST_TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze_project(project: dict, upload_dir: Path) -> Tuple[dict, str]:
    """업로드된 ZIP을 읽어 분석. 백엔드가 기록한 지문과 같은 파일일 때만 분석한다."""
    path = upload_dir / f"{project['id']}.zip"
    if not path.is_file():
        raise RunnerError(f"소스 ZIP이 없습니다({path}). runner를 백엔드와 같은 PC에서 돌리고 UPLOAD_DIR을 맞춰 주세요.")
    digest = sha256_file(path)
    if digest != project.get("source_sha256"):
        raise RunnerError("소스 ZIP의 지문(SHA-256)이 프로젝트에 기록된 값과 다릅니다. 분석하지 않습니다.", permanent=True)
    try:
        files = files_from_zip(path.read_bytes())
    except SourceError as e:
        raise RunnerError(str(e), permanent=True) from None

    result = analyze_files(files).to_result(scale=None)
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


def run_once(api: Api, upload_dir: Path, state: State, clock: Callable[[], float] = time.time) -> Dict[str, int]:
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
            result, digest = analyze_project(project, upload_dir)
            api.record_analysis(pid, digest, result)
        except RunnerError as e:
            counts["failed"] += 1
            _fail(state, pid, str(e), e.permanent, now)
            continue
        except ApiError as e:
            counts["failed"] += 1
            if e.status in (0, 429) or e.status >= 500:
                _fail(state, pid, e.message, permanent=False, now=now)
            elif e.status == 422 and "비밀값" in e.message:
                # 백엔드 _reject_secret_fields 가 이름과 상관없이 모든 "이름=값"을 거절하는 문제(c665956) 때문일 수 있음
                _fail(state, pid, f"{e.message} (백엔드 비밀값 검사가 평범한 코드 문장도 거절하는 문제일 수 있음: ai/README.md)", True, now)
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
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api, state = Api(args.api_url), State()
    upload_dir = args.upload_dir.expanduser().resolve()
    log.info("시작: 백엔드 %s, 업로드 폴더 %s", api.base, upload_dir)

    try:
        while True:
            counts = run_once(api, upload_dir, state)
            if args.once:
                log.info("한 번 점검 끝: 기록 %d, 건너뜀 %d, 실패 %d", counts["recorded"], counts["skipped"], counts["failed"])
                return 0 if counts["failed"] == 0 else 1
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info("종료")
        return 0


if __name__ == "__main__":
    sys.exit(main())
