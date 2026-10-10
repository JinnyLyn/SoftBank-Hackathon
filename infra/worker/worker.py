#!/usr/bin/env python3
"""플랫폼 백엔드(back/)와 deploy.sh를 잇는 worker. 표준 라이브러리만 쓴다.

백엔드는 소스·분석 결과·계획·승인·배포 대기열을 저장만 하고 Terraform·Docker·AWS는 실행하지 않는다(back/API.md).
이 worker가 그 빈 자리를 채운다. 세 가지 일을 한다.

  planner   등록됐지만 아직 계획이 없는 프로젝트를 찾아, 분석 결과로 구성·비용을 정하고
            `deploy.sh up --plan-only`로 Terraform 계획을 만들어 백엔드에 등록한다(POST /api/plans).
  executor  사용자가 승인해 대기열에 들어간 작업을 가져와(claim) 이미지를 빌드하고 저장된 계획 그대로 적용한 뒤
            상태(deploying → healthy | failed)와 접속 주소를 보고한다.
  connector 사용자가 CloudFormation으로 만든 역할(콜백으로 백엔드에 저장된 계정 ID·역할 ARN)을 실제로 AssumeRole 해 보고
            `complete`(성공) 또는 `fail`(역할 쪽 문제로 끝내 실패)을 보고한다. 사용자 계정 연결은 기본 제품 흐름이 아니다(docs/PRODUCT_DIRECTION.md).

지키는 규칙(AGENTS.md)
  - 승인된 저장 plan만 적용한다. 적용 직전에 plan 파일의 SHA-256이 승인된 값과 같은지 확인한다.
  - Docker 빌드는 승인된 작업에서만 한다(planner는 이미지를 만들지 않는다).
  - 비밀(API 키·토큰·DB 비밀번호)은 로그·이벤트에 남기지 않는다. WORKER_API_TOKEN은 환경 변수로만 받는다.
  - 실패는 자동 롤백하지 않고 그대로 보고한다(확정 정책). 원인은 diagnose 결과를 이벤트에 담는다.
    제품 롤백은 새 plan·diff의 사용자 승인이 필요하며, 이 worker의 롤백 연동은 아직 미구현이다(README 참조).
  - LLM 호출은 하지 않는다. 분석 결과(result)는 다른 담당 모듈이 백엔드에 기록한 것을 읽기만 한다.
  - 연결 확인은 AssumeRole로 임시 자격 증명을 받지만 출력하지 않는다(`--query`로 역할 ARN만 읽는다). 확인 결과는 account_id·role_arn만 백엔드에 보낸다.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost  # noqa: E402

HERE = Path(__file__).resolve().parent
INFRA = HERE.parent
DEPLOY_ID_RE = re.compile(r"^[a-z0-9]{4,8}$")
HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
SECRET_KEY_RE = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE|CREDENTIAL|API_?KEY|ACCESS_?KEY)")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
# 연결 확인 입력. back/app/schemas.py의 ConnectionRoleCallbackIn 규칙과 같다(external_id는 main.py의 pc-<hex 32자>). \Z로 끝의 개행을 거부한다
AWS_ARN_NAME = r"[A-Za-z0-9+=,.@_-]+"
ROLE_ARN_RE = re.compile(rf"^arn:(aws|aws-us-gov|aws-cn):iam::(\d{{12}}):role(?:/{AWS_ARN_NAME})+\Z")
ASSUMED_ARN_RE = re.compile(rf"^arn:(aws|aws-us-gov|aws-cn):sts::(\d{{12}}):assumed-role/{AWS_ARN_NAME}/{AWS_ARN_NAME}\Z")
EXTERNAL_ID_RE = re.compile(r"^pc-[0-9a-f]{32}\Z")
ACCOUNT_ID_RE = re.compile(r"^\d{12}\Z")
AWS_ERROR_RE = re.compile(r"An error occurred \((\w+)\) when calling the AssumeRole operation")
# 역할 쪽 문제로 볼 오류 코드(신뢰 정책·ExternalId 불일치, 없는 역할, 비활성 리전). 그 밖의 오류(만료된 자격 증명·네트워크)는 worker 쪽 문제라 연결을 실패로 만들지 않는다
ROLE_SIDE_ERRORS = {"AccessDenied", "ValidationError", "RegionDisabledException"}
VERIFY_SESSION_NAME = "paved-clouds-verify"
ASSUME_ROLE_TIMEOUT = 60   # 초. 시험에서는 줄인다
# 로그를 백엔드로 보내기 전에 한 번 더 가린다(백엔드도 가리지만 호출 측에서도 비밀을 보내지 않아야 한다)
REDACT_RES = [
    re.compile(r"(?i)\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(secret|password|passwd|token|api[_-]?key|access[_-]?key)[A-Za-z_]*\s*[=:]\s*\S+"),
    re.compile(r"mysql://[^\s:@]+:[^\s@]+@"),
]
# 환경 변수 "값"에 들어 있으면 안 되는 비밀의 모양(이름이 무해해도 값이 비밀이면 계획 변수와 작업 정의에 평문으로 남는다)
SECRET_VALUE_RES = REDACT_RES + [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
]


class PlanError(Exception):
    """계획을 만들 수 없는 프로젝트(분석 결과 부족, 예산 초과 등)."""


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


@dataclass
class Config:
    api_url: str = "http://127.0.0.1:8000"
    token: str = field(default="", repr=False)   # repr에 찍히지 않게 한다
    deploy_sh: Path = INFRA / "scripts" / "deploy.sh"
    deployments_dir: Path = INFRA / "deployments"
    bash: str = field(default_factory=lambda: find_bash())
    aws_cmd: list = field(default_factory=lambda: ["aws"])   # 연결 확인에 쓰는 AWS CLI. 시험에서는 가짜 스크립트로 바꾼다
    region: str = ""
    poll_seconds: float = 5.0
    plan_timeout: int = 900
    build_timeout: int = 1200
    apply_timeout: int = 1500


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def redact(text):
    for rx in REDACT_RES:
        text = rx.sub("[가림]", text)
    return text


# --- 백엔드 API -------------------------------------------------------------------------------------
class Api:
    def __init__(self, base, token, timeout=30):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, method, path, body=None, raw=None):
        headers = {"Accept": "application/json"}
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if path.startswith("/api/worker/"):
            headers["X-Worker-Token"] = self.token   # worker 전용 경로에만 붙인다
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(text) if text.strip() else None)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(0, f"백엔드에 연결하지 못했습니다: {e}") from None

    def get(self, path):
        return self._call("GET", path)[1]

    def post(self, path, body=None):
        return self._call("POST", path, body=body)[1]

    def post_bytes(self, path, data):
        return self._call("POST", path, raw=data)[1]


# --- 하위 프로세스 ---------------------------------------------------------------------------------
def popen(cmd, cwd=None, env=None, merge_stderr=False):
    """새 프로세스 그룹(POSIX)으로 시작해서 시간 초과 때 자식(terraform·docker)까지 함께 종료할 수 있게 한다."""
    kwargs = {} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, **kwargs)


def kill_tree(proc):
    """bash만 죽이면 그 아래의 terraform apply·docker build가 계속 돌아 state 잠금을 쥐고 리소스를 만든다. 프로세스 트리 전체를 종료한다."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_streaming(cmd, timeout, on_line=None, cwd=None, env=None):
    """명령을 실행하며 출력(stdout+stderr)을 한 줄씩 on_line으로 넘긴다. (종료 코드, 전체 출력)을 돌려준다.

    시간 초과면 자식 프로세스까지 종료하고 출력 끝에 그 사실을 남긴다. on_line에서 난 예외는 읽기를 멈추지 않도록 삼킨다
    (보고 실패 때문에 apply 출력 읽기가 끊기면 최종 결과를 보고하지 못한다)."""
    lines = []
    timed_out = []
    with popen(cmd, cwd=cwd, env=env, merge_stderr=True) as proc:
        def expire():
            timed_out.append(True)
            kill_tree(proc)
        timer = threading.Timer(timeout, expire)
        timer.start()
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                lines.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception as e:   # noqa: BLE001
                        log(f"출력 처리 중 오류(무시하고 계속 읽습니다): {type(e).__name__}: {str(e)[:150]}")
            proc.wait()
        finally:
            timer.cancel()
    if timed_out:
        lines.append(f"[worker] 제한 시간({timeout}초)을 넘겨 작업과 하위 프로세스를 종료했습니다")
    return proc.returncode, "\n".join(lines)


def run_capture(cmd, timeout, cwd=None, env=None):
    """표준 출력과 표준 오류를 따로 받는다(이미지 주소처럼 표준 출력만 필요한 명령용). 시간 초과면 자식까지 종료하고 TimeoutExpired를 낸다."""
    with popen(cmd, cwd=cwd, env=env) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            proc.communicate()
            raise
    return proc.returncode, out, err


def child_env(cfg):
    env = dict(os.environ)
    env.pop("WORKER_API_TOKEN", None)   # 하위 프로세스(빌드·Terraform)에 토큰을 넘기지 않는다
    if cfg.region:
        env["AWS_REGION"] = cfg.region
    return env


def posix(path):
    """bash에 넘길 경로. Windows의 C:/... 는 Git Bash가 안정적으로 여는 /c/... 형태로 바꾼다(deploy.sh 시험에서 겪은 문제)."""
    s = Path(path).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m and os.name == "nt" else s


def find_bash():
    """실제로 스크립트를 실행할 수 있는 bash. Windows에서 PATH의 bash는 WSL 중계 실행 파일일 수 있어 직접 확인한다.

    WSL bash도 산술 식은 계산하지만 /c/Users/... 경로(posix())를 열지 못한다. 그래서 deploy.sh를 실제로 볼 수 있는지까지 확인한다."""
    cands = [os.environ.get("BASH_EXE"), shutil.which("bash")]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Git", "bin", "bash.exe"), os.path.join(base, "Programs", "Git", "bin", "bash.exe")]
    probe = posix(INFRA / "scripts" / "deploy.sh")
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        try:
            r = subprocess.run([c, "-c", f'[ -f "{probe}" ] && echo $((40+2))'], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip() == "42":
                return c
        except (OSError, subprocess.SubprocessError):
            continue
    return "bash"


def deploy(cfg, *args):
    return [cfg.bash, posix(cfg.deploy_sh), *args]


# --- planner ---------------------------------------------------------------------------------------
def app_config_from_analysis(result, tier):
    """분석 결과(result)에서 앱 설정과 Dockerfile 경로를 읽어 app-config.schema.json 규칙으로 검증한다.

    분석 결과의 계약(LLM 담당과 합의 필요):
      result["app_config"]  container_port, health_check_path, use_database, environment, init_command(선택)
      result["dockerfile"]  소스 안의 Dockerfile 상대 경로(생략하면 Dockerfile)
    task_size·min_tasks·max_tasks 는 LLM이 아니라 선택한 구성 단계(tier)가 정한다.
    """
    if not isinstance(result, dict) or not isinstance(result.get("app_config"), dict):
        raise PlanError("분석 결과에 app_config가 없습니다(container_port, health_check_path 등이 필요합니다)")
    c = result["app_config"]
    port = c.get("container_port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise PlanError(f"app_config.container_port가 올바르지 않습니다: {port!r}")
    health = c.get("health_check_path", "/")
    if not isinstance(health, str) or not HEALTH_PATH_RE.match(health) or len(health) > 128:
        raise PlanError(f"app_config.health_check_path가 올바르지 않습니다: {health!r}")
    env = c.get("environment", {})
    if not isinstance(env, dict) or len(env) > 20:
        raise PlanError("app_config.environment는 20개 이하의 객체여야 합니다")
    for k, v in env.items():
        if not isinstance(k, str) or not ENV_KEY_RE.match(k) or k == "DATABASE_URL" or SECRET_KEY_RE.search(k):
            raise PlanError(f"환경 변수 이름이 허용되지 않습니다(비밀로 보이거나 DATABASE_URL): {k!r}")
        if not isinstance(v, str):
            raise PlanError(f"환경 변수 값은 문자열이어야 합니다: {k}")
        if any(rx.search(v) for rx in SECRET_VALUE_RES):
            raise PlanError(f"환경 변수 {k}의 값이 비밀(키·토큰·접속 URL)처럼 보입니다. 값은 계획에 평문으로 남아서 허용하지 않습니다")
    init = c.get("init_command")
    if init is not None and (not isinstance(init, list) or len(init) > 10 or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in init)):
        raise PlanError("app_config.init_command는 비어 있지 않은 문자열 배열(최대 10개)이어야 합니다")
    dockerfile = result.get("dockerfile", "Dockerfile")
    if not isinstance(dockerfile, str) or not dockerfile or dockerfile.startswith("/") or ".." in dockerfile or len(dockerfile) > 200:
        raise PlanError(f"dockerfile 경로가 올바르지 않습니다: {dockerfile!r}")
    t = cost.TIERS[tier]
    app = {"container_port": port, "health_check_path": health, "task_size": t["task_size"],
           "min_tasks": t["min_tasks"], "max_tasks": t["max_tasks"],
           "use_database": bool(c.get("use_database", False)), "environment": dict(env)}
    if init:
        app["init_command"] = list(init)
    return app, dockerfile


def scale_from_analysis(result):
    """사용 규모·예산. 백엔드 API에 받는 곳이 없어서 분석 결과의 scale 필드에서 읽는다(없으면 기본값)."""
    s = result.get("scale") if isinstance(result, dict) else None
    s = s if isinstance(s, dict) else {}
    # 분석 결과는 외부 모듈이 만든 값이라 타입을 믿지 않는다. 리스트·객체가 들어오면 cost.recommend의 딕셔너리 조회가
    # 처리되지 않은 TypeError를 내서 worker 전체가 멈췄다(프로젝트 하나의 잘못된 입력은 그 프로젝트의 오류로만 처리한다)
    users, pattern = s.get("expected_users"), s.get("traffic_pattern")
    for name, value in (("expected_users", users), ("traffic_pattern", pattern)):
        if value is not None and not isinstance(value, str):
            raise PlanError(f"scale.{name}는 문자열이어야 합니다: {type(value).__name__}")
    budget = s.get("monthly_budget_usd")
    if budget is not None:
        if isinstance(budget, bool):   # float(True) == 1.0 이라 숫자처럼 통과해 버린다
            raise PlanError("scale.monthly_budget_usd가 숫자가 아닙니다: bool")
        try:
            budget = float(budget)
        except (TypeError, ValueError):
            raise PlanError(f"scale.monthly_budget_usd가 숫자가 아닙니다: {type(budget).__name__}") from None
        if not math.isfinite(budget):   # NaN·inf는 예산 비교를 모두 거짓으로 만든다
            raise PlanError("scale.monthly_budget_usd는 유한한 숫자여야 합니다")
        if budget < 0:
            raise PlanError("scale.monthly_budget_usd는 0 이상이어야 합니다")
    return users, pattern, budget


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discard_unapplied(cfg, deploy_id):
    """계획만 만들고 적용하지 않은 배포 폴더를 지운다(백엔드 등록 실패 뒤 같은 ID로 다시 시도할 수 있게).
    state나 이력이 하나라도 있으면(적용된 적이 있으면) 지우지 않는다."""
    d = cfg.deployments_dir / deploy_id
    if not d.is_dir():
        return
    applied = list(d.glob("*.tfstate*")) + [d / "history.log", d / "applied.app.json"]
    if any(p.exists() for p in applied):
        return
    shutil.rmtree(d, ignore_errors=True)


def fetch_foundation(cfg, env):
    """배포된 foundation의 **최신** 출력을 deploy.sh foundation-info로 읽는다(읽기 전용. foundation.json 캐시도 갱신한다).

    비용은 이 값으로 계산해야 한다. 캐시 파일은 foundation을 다시 apply하기 전의 옛 구성일 수 있어, 읽기 전에 비용을 확정하면
    예산을 넘는 구성이 추천된다. 읽지 못하면 옛 캐시로 대신하지 않고 PlanError로 멈춘다.
    foundation-info는 compact JSON을 마지막 줄에 낸다(앞 줄은 진행 로그일 수 있다). 빈 객체면 None(기본 가정을 쓴다)."""
    rc, out, err = run_capture(deploy(cfg, "foundation-info"), 120, cwd=str(INFRA), env=env)
    if rc != 0:
        raise PlanError("foundation 정보를 읽지 못해 비용을 계산할 수 없습니다(foundation이 apply됐는지, 자격 증명을 확인하세요): "
                        + redact((err or out).strip())[-200:])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    try:
        d = json.loads(lines[-1]) if lines else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise PlanError("foundation 정보를 해석하지 못했습니다(deploy.sh foundation-info의 출력이 JSON이 아닙니다)")
    return d or None


def upload_plan(api, plan_id, plan_file):
    """plan 파일을 백엔드에 올린다. 연결 실패·5xx는 몇 번 다시 시도한다."""
    for attempt in range(REPORT_RETRIES):
        try:
            return api.post_bytes(f"/api/worker/plans/{plan_id}/terraform-plan", Path(plan_file).read_bytes())
        except ApiError as e:
            if not (e.status == 0 or e.status >= 500) or attempt == REPORT_RETRIES - 1:
                raise
            time.sleep(REPORT_RETRY_DELAY)


ACTIVE_PLAN_STATUSES = ("awaiting_approval", "approved", "consumed")   # superseded만 남은 프로젝트는 다시 계획한다


def repair_plan_uploads(api, cfg, plans):
    """백엔드에 등록됐지만 plan 파일이 올라가지 않은 계획(업로드 실패, worker 재시작)의 파일을 다시 올린다.
    승인된 계획과 같은 파일만 올린다: 로컬 파일의 SHA-256이 등록된 값과 다르면 올리지 않는다.
    Returns: 아직 올리지 못한 계획이 남아 있으면 True."""
    pending = False
    for plan in plans:
        if plan.get("status") != "awaiting_approval" or plan.get("terraform_plan_ready") is not False:
            continue
        deploy_id = (plan.get("variables") or {}).get("deploy_id", "")
        f = cfg.deployments_dir / deploy_id / "tfplan" if DEPLOY_ID_RE.match(deploy_id) else None
        if not f or not f.is_file() or sha256_file(f) != plan.get("terraform_plan_sha256"):
            log(f"계획 {plan.get('id')}: 로컬 plan 파일이 없거나 등록된 SHA-256과 달라 올리지 않습니다")
            continue
        try:
            upload_plan(api, plan["id"], f)
            log(f"계획 {plan['id']}: plan 파일을 다시 올렸습니다")
        except ApiError as e:
            log(f"계획 {plan['id']}: plan 파일 업로드가 다시 실패했습니다: {e}")
            pending = True
    return pending


def find_registered_plan(api, project_id, digest):
    """프로젝트의 계획 중 plan 파일 SHA-256이 digest인 것을 서버에서 찾아 돌려준다. 서버에 닿았는데 없으면 None.
    조회 자체가 실패하면 ApiError를 그대로 올린다(등록 여부를 모르는 상태와 '없음'을 구분하려고).
    등록 요청의 응답만 유실됐는지(서버에는 등록됐는지) 확인하는 데 쓴다."""
    for p in api.get(f"/api/projects/{project_id}/plans") or []:
        if p.get("terraform_plan_sha256") == digest:
            return p
    return None


def plan_project(api, cfg, project, analysis, prices=None, arch="X86_64"):
    """프로젝트 하나의 배포 계획을 만들어 백엔드에 등록하고 plan 파일을 올린다. 등록한 계획(PlanOut)을 돌려준다.
    plan 파일 업로드가 끝내 실패하면 계획은 등록된 채 파일을 남기고, 돌려주는 계획에 _upload_pending=True 를 붙인다(다음 점검에서 다시 올린다)."""
    prices = prices or cost.load_prices()
    src_sha = project.get("source_sha256")
    if not isinstance(src_sha, str) or not SHA256_RE.match(src_sha):
        raise PlanError("프로젝트의 source_sha256을 읽지 못했습니다(승인한 소스를 배포 직전에 확인하려면 필요합니다)")
    result = analysis.get("result", {})
    users, pattern, budget = scale_from_analysis(result)   # 입력 검증을 먼저 한다(잘못된 입력에 deploy.sh를 부르지 않는다)
    app_config_from_analysis(result, "lean")   # 앱 설정도 검증만 먼저 한다. 단계별 크기는 아래에서 고른 단계로 다시 정한다
    env = child_env(cfg)
    foundation = fetch_foundation(cfg, env)   # 비용을 확정하기 전에 최신 foundation 구성을 읽는다
    rec = cost.recommend(users, pattern, budget, prices, arch, foundation)
    if rec["recommended"] is None:
        raise PlanError(rec["reason"])
    tier = rec["recommended"]
    est = rec["estimates"][tier]
    app, dockerfile = app_config_from_analysis(result, tier)

    rc, out, err = run_capture(deploy(cfg, "make-id", f"{project['name']}|{project['id']}"), 60, env=env)
    deploy_id = out.strip()
    if rc != 0 or not DEPLOY_ID_RE.match(deploy_id):
        raise PlanError(f"배포 ID를 만들지 못했습니다: {err.strip()[-200:]}")
    rc, out, err = run_capture(deploy(cfg, "image-ref", deploy_id), 120, cwd=str(INFRA), env=env)
    image = out.strip().splitlines()[-1] if out.strip() else ""
    if rc != 0 or ":" not in image:
        raise PlanError(f"이미지 주소를 만들지 못했습니다: {err.strip()[-200:]}")

    # 같은 프로젝트를 다시 계획하는 경우(이전 계획이 superseded) 적용한 적 없는 이전 폴더는 지우고 새로 만든다.
    # 이미 적용된 배포의 폴더는 up이 거부하므로 미리 알린다(update 흐름이 필요하다)
    if (cfg.deployments_dir / deploy_id).exists():
        discard_unapplied(cfg, deploy_id)
        if (cfg.deployments_dir / deploy_id).exists():
            raise PlanError(f"배포 {deploy_id}는 이미 적용된 폴더가 있어 새로 계획할 수 없습니다(update로 바꾸세요)")

    tmp = Path(tempfile.mkdtemp(prefix="pc-app-"))
    try:
        app_file = tmp / "app.json"
        app_file.write_text(json.dumps(app, ensure_ascii=False), encoding="utf-8")
        log(f"계획 생성: 프로젝트 {project['id']} → 배포 ID {deploy_id}, 단계 {tier}")
        rc, out = run_streaming(deploy(cfg, "up", "--id", deploy_id, "--image", image, "--app", posix(app_file), "--arch", arch, "--plan-only"),
                                cfg.plan_timeout, cwd=str(INFRA), env=env)
        if rc != 0:
            raise PlanError("Terraform 계획을 만들지 못했습니다: " + redact(out)[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    plan_file = cfg.deployments_dir / deploy_id / "tfplan"
    if not plan_file.is_file():
        raise PlanError("계획 파일(tfplan)이 만들어지지 않았습니다")
    digest = sha256_file(plan_file)

    variables = {
        "deploy_id": deploy_id, "image": image, "dockerfile": dockerfile, "app": app,
        # 승인된 소스(ZIP)의 SHA-256과 이미지 아키텍처: executor가 빌드 직전에 확인하고 같은 아키텍처로 빌드한다
        "source_sha256": src_sha, "cpu_architecture": arch,
        "tier": tier, "recommended": True, "headline": est["headline"], "tradeoff": est["tradeoff"], "reason": rec["reason"],
        "resources": est["resources"],
        "cost": {"app_monthly": est["app_monthly"], "shared_monthly": est["shared_monthly"], "excluded": est["excluded"]},
    }
    summary = "\n".join([
        f"{est['label']} 구성({tier}): {app['task_size']} 태스크 {app['min_tasks']}개(최대 {app['max_tasks']}개), 포트 {app['container_port']}, "
        f"헬스체크 {app['health_check_path']}, 앱 전용 DB {'사용' if app['use_database'] else '미사용'}",
        f"월 추정 ${est['total_monthly']:.2f} = 앱 추가 ${est['app_monthly']:.2f} + 공용(ALB·RDS·공인 IPv4) ${est['shared_monthly']:.2f}",
        f"기준: {est['region']}, {est['pricing_as_of']} 가격표. 제외: " + "; ".join(est["excluded"]),
        f"선택 이유: {rec['reason']}",
    ])
    body = {
        "project_id": project["id"], "analysis_id": analysis.get("id"), "target": "aws", "module_id": "ecs-web-app",
        "variables": variables, "summary": summary,
        "cost_estimate": {"amount": f"{est['total_monthly']:.4f}", "currency": est["currency"], "period": "month",
                          "pricing_as_of": est["pricing_as_of"]},
        "terraform_plan_sha256": digest,
    }
    try:
        plan = api.post("/api/plans", body)
    except ApiError as e:
        if e.status != 0 and e.status < 500:
            discard_unapplied(cfg, deploy_id)   # 서버가 거부했다(4xx) = 등록되지 않았으니 같은 프로젝트를 다시 시도할 수 있게 한다
            raise
        # 연결이 끊겼거나 5xx: 서버는 등록을 끝냈는데 응답만 잃었을 수 있다. 로컬 plan 파일을 지우면 올릴 파일이 없어
        # 승인된 작업이 plan_mismatch로 영영 실패하므로, 먼저 서버에 등록됐는지 확인한다
        try:
            plan = find_registered_plan(api, project["id"], digest)
        except ApiError:
            # 확인도 못 했다: 등록됐는지 모르니 파일을 남긴다. 다음 점검에서 서버 목록에 계획이 보이면 repair_plan_uploads가 올린다
            log(f"계획 등록 여부를 확인하지 못했습니다(파일은 남깁니다, 다음 점검에서 다시 확인): {e}")
            raise e from None
        if plan is None:
            discard_unapplied(cfg, deploy_id)   # 서버에 없다 = 등록되지 않았으니 같은 프로젝트를 다시 시도할 수 있게 한다
            raise
        log(f"등록 응답은 유실됐지만 서버에 계획 {plan['id']}이 있습니다. plan 파일 업로드를 이어갑니다")
    try:
        upload_plan(api, plan["id"], plan_file)
    except ApiError as e:
        # 계획은 이미 등록됐다. 로컬 plan 파일을 지우면 승인된 작업이 plan_mismatch로 영영 실패하므로 남기고 다시 올린다
        log(f"plan 파일 업로드 실패(계획 {plan['id']}은 등록됨, 파일은 남겨 다시 올립니다): {e}")
        plan["_upload_pending"] = True
        return plan
    log(f"계획 등록 완료: {plan['id']} (월 ${est['total_monthly']:.2f}, sha {digest[:12]})")
    return plan


# --- executor --------------------------------------------------------------------------------------
REPORT_RETRIES = 4
REPORT_RETRY_DELAY = 2.0   # 초. 시험에서는 0으로 줄인다


def report(api, deployment_id, status, event_type, message, level="info", details=None, url=None):
    """배포 상태 이벤트를 보낸다. 연결 실패나 5xx는 몇 번 다시 시도한다(결과 보고가 한 번의 일시 오류로 사라지지 않게).
    4xx(상태 전이 거부 등)는 다시 시도해도 같으니 바로 올린다."""
    body = {"status": status, "level": level, "event_type": event_type, "message": message[:4000]}
    if details is not None:
        body["details"] = details
    if url:
        body["url"] = url
    for attempt in range(REPORT_RETRIES):
        try:
            return api.post(f"/api/worker/deployments/{deployment_id}/events", body)
        except ApiError as e:
            transient = e.status == 0 or e.status >= 500
            if not transient or attempt == REPORT_RETRIES - 1:
                raise
            time.sleep(REPORT_RETRY_DELAY)


def tail(text, n=40):
    return redact("\n".join(text.splitlines()[-n:]))[-6000:]


def fail_job(api, deployment_id, event_type, message, details=None):
    log(f"배포 실패({event_type}): {message}")
    report(api, deployment_id, "failed", event_type, message, level="error", details=details)


def execute_job(api, cfg, job):
    """claim한 작업 하나를 실행한다. 어떤 경우에도 결과를 failed 또는 healthy 로 보고한다(처리 도중 멈춘 채 두지 않는다)."""
    dep = job["deployment_id"]
    v = job.get("variables") or {}
    deploy_id = v.get("deploy_id", "")
    image = v.get("image", "")
    dockerfile = v.get("dockerfile", "Dockerfile")
    try:
        if not DEPLOY_ID_RE.match(deploy_id):
            return fail_job(api, dep, "invalid_job", f"작업의 deploy_id가 올바르지 않습니다: {deploy_id!r}")
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        if not TAG_RE.match(tag):
            return fail_job(api, dep, "invalid_job", "작업의 이미지 주소에서 태그를 읽지 못했습니다")
        plan_file = cfg.deployments_dir / deploy_id / "tfplan"
        expected = job.get("terraform_plan_sha256") or ""
        if not SHA256_RE.match(expected) or not plan_file.is_file() or sha256_file(plan_file) != expected:
            return fail_job(api, dep, "plan_mismatch",
                            "저장된 Terraform 계획이 승인된 계획과 다릅니다(파일이 없거나 SHA-256이 다릅니다). 적용하지 않았습니다")
        # 승인한 소스와 지금 소스가 같은지 확인한다. plan 해시는 인프라 계획만 덮으므로, 승인 뒤에 소스 파일이 바뀌면
        # 사용자가 검토하지 않은 코드가 배포된다(AGENTS.md 4장: 승인 이후 내용이 바뀌면 승인을 폐기한다)
        src_expected = v.get("source_sha256", "")
        src_path = job.get("source_path", "")
        job_sha = job.get("source_sha256")
        if (not isinstance(src_expected, str) or not SHA256_RE.match(src_expected) or (job_sha and job_sha != src_expected)
                or not src_path or not Path(src_path).is_file() or sha256_file(src_path) != src_expected):
            return fail_job(api, dep, "source_mismatch",
                            "승인된 계획의 소스(ZIP)와 지금 소스가 다릅니다(파일이 없거나 SHA-256이 다릅니다). 빌드하지 않았습니다")
        arch = v.get("cpu_architecture")
        if arch not in ("X86_64", "ARM64"):
            return fail_job(api, dep, "invalid_job", f"작업의 cpu_architecture가 올바르지 않습니다: {arch!r}")
        env = child_env(cfg)

        log(f"이미지 빌드: {deploy_id} ({arch})")
        rc, out, err = run_capture(deploy(cfg, "build", "--id", deploy_id, "--source", posix(src_path),
                                          "--dockerfile", dockerfile, "--tag", tag, "--arch", arch),
                                   cfg.build_timeout, cwd=str(INFRA), env=env)
        built = out.strip().splitlines()[-1] if out.strip() else ""
        if rc != 0:
            return fail_job(api, dep, "build_failed", "이미지를 빌드하거나 올리지 못했습니다", {"log_tail": tail(err)})
        if built != image:
            return fail_job(api, dep, "image_mismatch", "빌드한 이미지가 계획에 적힌 이미지와 다릅니다. 적용하지 않았습니다",
                            {"planned": image, "built": built})

        state = {"deploying": False}

        def on_line(line):
            # deploy.sh apply는 DB·Terraform 적용(provisioning)을 끝내고 헬스체크를 기다리기 시작할 때 이 문구를 남긴다
            # 보고에 성공했을 때만 표시한다. 실패하면 끝난 뒤 healthy 보고 전에 다시 보낸다(백엔드는 provisioning에서 healthy로 바로 갈 수 없다)
            if "헬스체크 대기" in line and not state["deploying"]:
                report(api, dep, "deploying", "apply_done", "인프라 적용이 끝났고 헬스체크를 기다립니다")
                state["deploying"] = True

        log(f"적용: {deploy_id}")
        rc, out = run_streaming(deploy(cfg, "apply", deploy_id), cfg.apply_timeout, on_line, cwd=str(INFRA), env=env)
        if rc != 0:
            details = {"log_tail": tail(out)}
            drc, dout, derr = run_capture(deploy(cfg, "diagnose", deploy_id), 180, cwd=str(INFRA), env=env)
            if drc == 0 and dout.strip():
                try:
                    details["diagnose"] = json.loads(dout)
                except ValueError:
                    pass
            if len(json.dumps(details, ensure_ascii=False).encode("utf-8")) > 30 * 1024:
                details.pop("diagnose", None)   # 백엔드 한도(32 KiB)를 넘으면 진단 JSON은 빼고 로그만 보낸다
            return fail_job(api, dep, "apply_failed", "배포가 실패했습니다. 이력과 진단은 details를 확인하세요", details)

        outputs = cfg.deployments_dir / deploy_id / "outputs.json"
        try:
            url = json.loads(outputs.read_text(encoding="utf-8"))["url"]["value"]
        except (OSError, ValueError, KeyError):
            return fail_job(api, dep, "no_url", "배포는 끝났지만 접속 주소(outputs.json의 url)를 읽지 못했습니다", {"log_tail": tail(out)})
        if not state["deploying"]:
            report(api, dep, "deploying", "apply_done", "인프라 적용이 끝났습니다")
        report(api, dep, "healthy", "healthy", "헬스체크를 통과했습니다", details={"log_tail": tail(out, 12)}, url=url)
        log(f"배포 완료: {url}")
    except ApiError as e:
        log(f"백엔드 보고 실패: {e}")   # 보고가 안 되면 더 할 수 있는 것이 없다. 작업은 다음 점검에서 사람이 확인한다
    except Exception as e:   # noqa: BLE001 - 예상 못 한 오류도 failed로 남긴다
        try:
            fail_job(api, dep, "worker_error", f"worker 오류: {type(e).__name__}: {redact(str(e))[:300]}")
        except ApiError as e2:
            log(f"백엔드 보고 실패: {e2}")


# --- connector -------------------------------------------------------------------------------------
def post_retry(api, path, body):
    """worker 경로로 보낸다. 연결 실패·5xx는 몇 번 다시 시도하고 4xx는 바로 올린다(report와 같은 규칙)."""
    for attempt in range(REPORT_RETRIES):
        try:
            return api.post(path, body)
        except ApiError as e:
            if not (e.status == 0 or e.status >= 500) or attempt == REPORT_RETRIES - 1:
                raise
            time.sleep(REPORT_RETRY_DELAY)


def check_role(cfg, env, role_arn, external_id, account_id):
    """사용자 역할을 실제로 AssumeRole 해 본다. (결과, 설명)을 돌려준다. 설명은 로그용이고 사용자에게 보이는 오류에는 쓰지 않는다.

    결과: "ok"      역할을 맡을 수 있고, 맡은 역할이 보고된 계정의 것이다
          "denied"  역할 쪽 문제(신뢰 정책·ExternalId 불일치, 없는 역할, 다른 계정의 역할). 방금 만든 역할은 IAM 전파 때문에 잠깐 실패할 수 있다
          "error"   worker 쪽 문제(만료된 자격 증명, 네트워크, CLI 없음). 연결을 실패로 만들지 않는다
    임시 자격 증명은 출력하지 않는다: --query 로 AssumedRoleUser.Arn 만 읽는다."""
    cmd = [*cfg.aws_cmd, "sts", "assume-role", "--role-arn", role_arn, "--role-session-name", VERIFY_SESSION_NAME,
           "--external-id", external_id, "--duration-seconds", "900", "--query", "AssumedRoleUser.Arn", "--output", "text"]
    try:
        rc, out, err = run_capture(cmd, ASSUME_ROLE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return "error", f"aws sts assume-role이 {ASSUME_ROLE_TIMEOUT}초 안에 끝나지 않았습니다"
    except OSError as e:
        return "error", f"aws CLI를 실행하지 못했습니다: {type(e).__name__}"
    if rc != 0:
        m = AWS_ERROR_RE.search(err)
        code = m.group(1) if m else "unknown"
        return ("denied" if code in ROLE_SIDE_ERRORS else "error"), f"{code}: {redact((err or out).strip())[-300:]}"
    m = ASSUMED_ARN_RE.match(out.strip())
    if not m:
        return "error", "AssumeRole 결과의 ARN을 해석하지 못했습니다"
    if m.group(2) != account_id:
        return "denied", f"맡은 역할의 계정({m.group(2)})이 보고된 계정과 다릅니다"
    return "ok", ""


def verify_connection(api, cfg, state, env, conn):
    """대기 연결 하나를 처리한다. 콜백으로 역할 정보가 아직 오지 않았으면 아무것도 하지 않고 기다린다(back/API.md)."""
    cid, account_id, role_arn, external_id = conn.get("id"), conn.get("account_id"), conn.get("role_arn"), conn.get("external_id")
    if account_id is None and role_arn is None:
        return   # CloudFormation 콜백 전
    role = ROLE_ARN_RE.match(role_arn) if isinstance(role_arn, str) else None
    if (not role or not isinstance(account_id, str) or not ACCOUNT_ID_RE.match(account_id) or role.group(2) != account_id
            or not isinstance(external_id, str) or not EXTERNAL_ID_RE.match(external_id)):
        log(f"연결 {cid}: 역할 정보 형식이 올바르지 않아 건너뜁니다(백엔드 저장값을 확인하세요)")
        state.conn_blocked.add(cid)
        return
    tries, last = state.conn_tries.get(cid, (0, 0.0))
    if tries and time.time() - last < state.conn_retry_after:
        return
    result, why = check_role(cfg, env, role_arn, external_id, account_id)
    if result == "ok":
        try:
            post_retry(api, f"/api/worker/connections/{cid}/complete", {"account_id": account_id, "role_arn": role_arn})
        except ApiError as e:
            log(f"연결 {cid}: 완료 보고 실패({e})")
            if e.status == 0 or e.status >= 500:
                state.conn_tries[cid] = (tries, time.time())   # 일시 오류: 다음 점검에서 다시 보고한다
            else:
                state.conn_blocked.add(cid)   # 4xx(저장된 값과 불일치 등): 다시 보내도 같다. 사람이 확인한다
            return
        state.conn_tries.pop(cid, None)
        log(f"연결 확인 완료: {cid} (계정 {account_id})")
        return
    if result == "error":
        # worker 쪽 문제다. 횟수를 올리지 않아 사용자의 연결이 실패로 끝나지 않는다
        log(f"연결 {cid}: 역할을 확인하지 못했습니다(worker 환경 문제일 수 있어 다시 시도합니다): {why}")
        state.conn_tries[cid] = (tries, time.time())
        return
    tries += 1
    state.conn_tries[cid] = (tries, time.time())
    log(f"연결 {cid}: AssumeRole 거부 {tries}/{state.conn_max_tries}회: {why}")
    if tries < state.conn_max_tries:
        return   # 방금 만든 역할은 IAM 전파가 끝나기 전에 잠깐 거부될 수 있다
    # 사용자에게 보이는 오류에는 AWS 원문을 넣지 않는다(운영자 계정의 IAM 주체 이름이 들어 있다). ExternalId도 넣지 않는다
    code = why.split(":", 1)[0]
    msg = (f"AWS 역할({role_arn})을 맡지 못했습니다({code}). CloudFormation 스택의 신뢰 정책(운영자 계정·ExternalId)과 "
           f"역할이 만들어졌는지 확인한 뒤 연결을 다시 만들어 주세요")
    try:
        post_retry(api, f"/api/worker/connections/{cid}/fail", {"error": msg})
    except ApiError as e:
        log(f"연결 {cid}: 실패 보고 실패({e})")
        if not (e.status == 0 or e.status >= 500):
            state.conn_blocked.add(cid)
        return
    state.conn_tries.pop(cid, None)
    log(f"연결 실패 보고: {cid}")


def verify_connections(api, cfg, state):
    """백엔드의 대기 중인 AWS 연결을 확인한다. 이 일이 실패해도 배포 작업(claim·계획)은 계속한다."""
    try:
        pending = api.get("/api/worker/connections/pending") or []
    except ApiError as e:
        if not state.conn_warned:   # 5초마다 같은 줄이 반복되지 않게 처음 한 번만 알린다
            log(f"대기 중인 연결 목록을 읽지 못했습니다(백엔드에 연결 API가 없거나 일시 오류): {e}")
            state.conn_warned = True
        return
    state.conn_warned = False
    if not isinstance(pending, list):
        return
    env = child_env(cfg)
    live = set()
    for conn in pending:
        cid = conn.get("id") if isinstance(conn, dict) else None
        if not isinstance(cid, str):
            continue
        live.add(cid)
        if cid in state.conn_blocked:
            continue
        try:
            verify_connection(api, cfg, state, env, conn)
        except Exception as e:  # noqa: BLE001 - 연결 하나의 예상 못 한 오류가 다른 연결과 배포 작업을 멈추지 않게 한다
            state.conn_tries[cid] = (state.conn_tries.get(cid, (0, 0.0))[0], time.time())
            log(f"연결 {cid}: 처리 중 오류: {type(e).__name__}: {redact(str(e))[:200]}")
    # 완료·실패·삭제돼 목록에서 사라진 연결의 기록은 버린다
    for k in [k for k in state.conn_tries if k not in live]:
        del state.conn_tries[k]
    state.conn_blocked &= live


# --- 반복 ------------------------------------------------------------------------------------------
@dataclass
class State:
    failures: dict = field(default_factory=dict)   # 프로젝트 id → (실패 횟수, 마지막 시각)
    done: dict = field(default_factory=dict)       # 프로젝트 id → 활성 계획이 있는 것을 마지막으로 확인한 시각(time.time())
    repair: set = field(default_factory=set)       # plan 파일 업로드가 남은 프로젝트(점검마다 다시 올린다)
    recheck_after: float = 30.0                    # 활성 계획이 있는 프로젝트의 계획 상태를 다시 조회하는 간격(초). 계획이 superseded로 바뀌면 다시 계획한다
    retry_after: float = 120.0
    max_tries: int = 3
    conn_tries: dict = field(default_factory=dict)   # 연결 id → (AssumeRole 거부 횟수, 마지막 시도 시각)
    conn_blocked: set = field(default_factory=set)   # 형식 오류·4xx로 이 프로세스에서 더 다루지 않는 연결
    conn_warned: bool = False                        # 대기 연결 목록 조회 실패를 이미 알렸는지
    conn_retry_after: float = 15.0                   # 같은 연결을 다시 확인하기까지의 간격(초)
    conn_max_tries: int = 8                          # 역할 쪽 거부가 이만큼 이어지면 실패로 보고한다(약 2분)


def list_projects(api, max_pages=10):
    """프로젝트 목록 전체(next_cursor를 따라간다). 첫 페이지만 읽으면 오래된 프로젝트가 앞을 채울 때 새 프로젝트가 영영 계획되지 않는다."""
    items, cursor = [], None
    for _ in range(max_pages):
        path = "/api/projects?limit=20" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        page = api.get(path) or {}
        items.extend(page.get("items", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return items


def plan_pending(api, cfg, state, prices=None, arch="X86_64"):
    for project in list_projects(api):
        pid = project["id"]
        # 활성 계획이 있는 프로젝트도 recheck_after마다 다시 조회한다(같은 프로세스에서 계획이 superseded로 바뀔 수 있다)
        if pid in state.done and pid not in state.repair and time.time() - state.done[pid] < state.recheck_after:
            continue
        tries, last = state.failures.get(pid, (0, 0.0))
        if pid not in state.repair and (tries >= state.max_tries or (tries and time.time() - last < state.retry_after)):
            continue
        plans = api.get(f"/api/projects/{pid}/plans") or []
        if repair_plan_uploads(api, cfg, plans):
            state.repair.add(pid)
        else:
            state.repair.discard(pid)
        # 진행 중이거나 끝난 계획(승인 대기·승인·배포됨)이 하나라도 있으면 건너뛴다. 모두 superseded(대체됨)이면 다시 계획한다
        if any(p.get("status") in ACTIVE_PLAN_STATUSES for p in plans):
            state.done[pid] = time.time()
            continue
        state.done.pop(pid, None)   # 활성 계획이 없다: 다시 계획할 대상이다
        try:
            analysis = api.get(f"/api/projects/{pid}/analyses/latest")
        except ApiError as e:
            if e.status == 404:
                continue   # 분석 결과가 아직 없다. 분석 담당 모듈이 기록할 때까지 기다린다
            raise
        try:
            plan = plan_project(api, cfg, project, analysis, prices, arch)
            state.done[pid] = time.time()
            if plan.get("_upload_pending"):
                state.repair.add(pid)
        except Exception as e:  # noqa: BLE001 - 프로젝트 하나의 예상 못 한 오류(잘못된 분석 결과 등)가 worker 전체를 멈추지 않게 한다
            state.failures[pid] = (tries + 1, time.time())
            expected = isinstance(e, (PlanError, ApiError, subprocess.TimeoutExpired, OSError))
            kind = "" if expected else f"{type(e).__name__}: "   # 예상한 오류가 아니면 종류를 남겨 원인을 찾게 한다
            log(f"계획 생성 실패(프로젝트 {pid}, {tries + 1}/{state.max_tries}회): {kind}{redact(str(e))[:300]}")


def tick(api, cfg, state, prices=None, arch="X86_64"):
    """한 번 점검한다: 대기 중인 연결을 확인하고, 승인된 작업이 있으면 실행하고, 계획이 없는 프로젝트가 있으면 계획을 만든다.
    연결 확인은 몇 초면 끝나므로 길게 걸리는 배포 작업보다 먼저 한다."""
    verify_connections(api, cfg, state)
    got = api.post("/api/worker/deployments/claim")
    job = (got or {}).get("job")
    if job:
        log(f"작업 시작: 배포 {job['deployment_id']}")
        execute_job(api, cfg, job)
    plan_pending(api, cfg, state, prices, arch)
    return bool(job)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-url", default=os.environ.get("PLATFORM_API_URL", "http://127.0.0.1:8000"))
    p.add_argument("--deploy-sh", default=os.environ.get("DEPLOY_SH", str(INFRA / "scripts" / "deploy.sh")))
    p.add_argument("--poll", type=float, default=float(os.environ.get("POLL_SECONDS", "5")), help="점검 간격(초)")
    p.add_argument("--once", action="store_true", help="한 번만 점검하고 끝낸다")
    p.add_argument("--arch", default=os.environ.get("TARGET_ARCH", ""), help="X86_64 또는 ARM64(기본: 이 PC의 docker)")
    a = p.parse_args(argv)
    token = os.environ.get("WORKER_API_TOKEN", "")
    if not token:
        print("오류: WORKER_API_TOKEN 환경 변수가 필요합니다(백엔드와 같은 값)", file=sys.stderr)
        return 2
    cfg = Config(api_url=a.api_url, token=token, deploy_sh=Path(a.deploy_sh), region=os.environ.get("AWS_REGION", ""), poll_seconds=a.poll)
    arch = a.arch
    if not arch:
        rc, out, _ = run_capture(deploy(cfg, "detect-arch"), 30, env=child_env(cfg))
        arch = out.strip() if rc == 0 and out.strip() in ("X86_64", "ARM64") else "X86_64"
    api = Api(cfg.api_url, cfg.token)
    state = State()
    log(f"worker 시작: {cfg.api_url} (아키텍처 {arch})")
    while True:
        try:
            tick(api, cfg, state, arch=arch)
        except ApiError as e:
            log(f"백엔드 오류: {e}")
        if a.once:
            return 0
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
