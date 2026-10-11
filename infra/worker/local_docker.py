#!/usr/bin/env python3
"""발표자 PC의 로컬 Docker에 앱을 올리는 계획·실행 도우미(target=local_docker). 표준 라이브러리만 쓴다.

worker.py가 부르는 모듈이다. AWS 경로(deploy.sh·Terraform)와 별개로, 승인된 계획을 이 PC의 `docker compose`로 실행한다.
이 PC의 Docker만 대상이다. 원격 서버(SSH)·사용자 서버 연결·HTTPS·도메인은 없다(docs/OPEN_QUESTIONS.md).

AWS 경로와 같은 규칙
  - 승인된 계획만 실행한다. 계획의 본문은 compose 파일이고(`variables.compose_yaml`), 그 SHA-256이 `terraform_plan_sha256` 자리에 등록된다.
    실행기는 승인된 변수에서 compose 파일을 다시 만들어 본문과 같은지, SHA-256이 같은지 확인한 뒤에만 실행한다.
    그래서 이 모듈이 만든 모양(호스트 경로 마운트·docker 소켓·특권 모드가 없는 compose)이 아닌 파일은 실행되지 않는다.
  - 비밀(DB 비밀번호·DATABASE_URL)은 계획·compose 본문·로그에 넣지 않는다. 실행 때 만든 `secrets/*.env` 파일로만 컨테이너에 넘긴다.
  - 소스 ZIP은 신뢰하지 않는다(경로 이탈·심볼릭 링크·압축 폭탄 거부, deploy.sh의 extract_zip과 같은 규칙).
  - 실패해도 자동 롤백하지 않는다. 실패한 배포는 컨테이너를 멈추기만 하고(재시작 루프가 PC 자원을 쓰지 않게) 지우지 않는다.

운영자용 정리 명령
  python infra/worker/local_docker.py list
  python infra/worker/local_docker.py status  <배포ID>
  python infra/worker/local_docker.py destroy <배포ID> [--drop-data]   # --drop-data: DB 볼륨과 비밀 파일까지 지운다(되돌릴 수 없다)
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
INFRA = HERE.parent
sys.path.insert(0, str(HERE))
import cost  # noqa: E402

TARGET = "local_docker"                 # 백엔드 deployment_plans.target 값(back/migrations/001_initial.sql의 ENUM에 이미 있다)
MODULE_ID = "local-docker-compose"
CONNECTION_NAME = "발표자 PC (로컬 Docker)"
DB_IMAGE = "mysql:8.4"                  # 샘플·플랫폼이 기준으로 삼는 MySQL 8.4(infra/README.md)
PORT_MIN, PORT_MAX = 18001, 18999       # 호스트에 여는 포트 범위(OS 임시 포트 범위와 겹치지 않게)
MAX_FILES = 20000                       # ZIP 한도. deploy.sh의 extract_zip과 같다
MAX_RATIO = 200
ID_RE = re.compile(r"^[a-z0-9]{4,8}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
IMAGE_RE = re.compile(r"^pc-local/[a-z0-9]{4,8}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
BIND_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

# 구성 단계별 자원 한도. 컨테이너가 쓸 수 있는 상한이다(예약이 아니다). 비용은 0이라 자원 사용량으로 보여 준다.
PROFILES = {
    "lean": {"label": "작게 시작", "app_cpus": 0.5, "app_mem_mb": 512, "db_cpus": 1.0, "db_mem_mb": 1024,
             "headline": "컨테이너 하나로 가볍게 시작", "tradeoff": "PC를 끄거나 Docker가 멈추면 서비스도 멈춥니다"},
    "balanced": {"label": "권장", "app_cpus": 1.0, "app_mem_mb": 1024, "db_cpus": 1.0, "db_mem_mb": 1024,
                 "headline": "일반적인 소규모 서비스에 맞춘 자원 한도", "tradeoff": "컨테이너 하나라 앱이 멈추면 다시 뜰 때까지 끊깁니다(자동 재시작)"},
    "roomy": {"label": "여유 있게", "app_cpus": 2.0, "app_mem_mb": 2048, "db_cpus": 2.0, "db_mem_mb": 2048,
              "headline": "넉넉한 자원 한도", "tradeoff": "PC 자원을 많이 차지하고, 서버 한 대라 하드웨어 장애는 막지 못합니다"},
}
MEM_SHARE = 0.7   # 컨테이너 메모리 한도의 합이 Docker가 쓸 수 있는 메모리의 이 비율을 넘지 않게 단계를 낮춘다

# 로그를 백엔드로 보내기 전에 한 번 더 가린다(worker.REDACT_RES와 같은 규칙)
REDACT_RES = [
    re.compile(r"(?i)\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(secret|password|passwd|token|api[_-]?key|access[_-]?key)[A-Za-z_]*\s*[=:]\s*\S+"),
    re.compile(r"mysql://[^\s:@]+:[^\s@]+@"),
]


class LocalError(Exception):
    """로컬 Docker 계획·실행 실패. event_type은 백엔드 이벤트 종류, details는 이벤트에 붙일 마스킹된 정보."""

    def __init__(self, event_type, message, details=None):
        super().__init__(message)
        self.event_type = event_type
        self.message = message
        self.details = details


@dataclass
class LocalConfig:
    docker: list = field(default_factory=lambda: ["docker"])   # 시험에서는 가짜 docker 스크립트로 바꾼다
    root: Path = INFRA / "local-deployments"                   # 배포 1건 = <root>/<배포ID>/ (compose 파일, secrets/). Git에서 제외된다
    bind: str = "127.0.0.1"                                    # 컨테이너 포트를 여는 주소. 0.0.0.0이면 같은 네트워크의 다른 PC에서도 접속된다
    public_host: str = "localhost"                             # 접속 주소에 쓸 이름(발표 화면·휴대폰에서 열 주소)
    build_timeout: int = 1200
    up_timeout: int = 600
    init_timeout: int = 300
    health_timeout: int = 180
    poll_seconds: float = 2.0


def redact(text, known=()):
    """로그에서 비밀을 가린다. known은 이번 배포에서 만든 비밀 값(비밀번호 등)이며 모양과 상관없이 통째로 가린다."""
    for s in known:
        if s and len(s) >= 6:
            text = text.replace(s, "[가림]")
    for rx in REDACT_RES:
        text = rx.sub("[가림]", text)
    return text


def tail(text, n=40, known=()):
    return redact("\n".join(text.splitlines()[-n:]), known)[-6000:]


# --- 계획 도우미 -----------------------------------------------------------------------------------
def local_deploy_id(project_id):
    """프로젝트 ID에서 정한 배포 ID(소문자·숫자 8자). 같은 프로젝트는 항상 같은 ID라 다시 계획해도 같은 이름·볼륨을 쓴다."""
    return hashlib.sha256(str(project_id).encode("utf-8")).hexdigest()[:8]


def image_ref(deploy_id, source_sha256):
    """이 PC의 로컬 이미지 이름. 소스가 같으면 같은 태그다(레지스트리가 없어 push하지 않는다)."""
    return f"pc-local/{deploy_id}:s{source_sha256[:12]}"


def port_is_free(bind, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_host_port(deploy_id, bind="127.0.0.1", taken=()):
    """배포 ID에서 정한 시작 포트부터 비어 있는 첫 포트를 고른다. 같은 PC에서 두 계획이 같은 포트를 고르면 늦게 띄우는 쪽이 실패한다."""
    span = PORT_MAX - PORT_MIN + 1
    start = int(hashlib.sha256(deploy_id.encode("utf-8")).hexdigest()[:8], 16) % span
    for i in range(span):
        port = PORT_MIN + (start + i) % span
        if port not in taken and port_is_free(bind, port):
            return port
    raise LocalError("no_free_port", f"{PORT_MIN}~{PORT_MAX} 범위에 비어 있는 포트가 없습니다")


def recommend_tier(expected_users, pattern):
    """예상 사용자 수와 접속 패턴으로 시작 단계를 고른다(AWS 계획의 cost.recommend와 같은 규칙, 비용 조건만 없다). (단계, 이유 목록)."""
    base = cost.USERS_TO_TIER.get(expected_users, "balanced")
    idx = cost.ORDER.index(base)
    if expected_users in cost.USERS_TO_TIER:
        reasons = [f"예상 사용자 {expected_users}명에 맞는 {PROFILES[base]['label']} 단계"]
    else:
        reasons = ["예상 사용자 수를 몰라 권장 단계로 시작"]
    if pattern == "peak" and idx < len(cost.ORDER) - 1:
        idx += 1
        reasons.append("특정 시간에 접속이 몰려 한 단계 올림")
    return cost.ORDER[idx], reasons


def fit_tier(tier, use_database, ncpu, mem_mb):
    """Docker가 쓸 수 있는 메모리 안에 한도의 합이 들어가는 가장 큰 단계로 낮춘다. 가장 작은 단계도 못 담으면 LocalError.

    반환: (단계, 한도가 담긴 프로필 사본, 낮춘 이유 목록). cpus는 Docker가 가진 CPU 수를 넘을 수 없어(넘으면 컨테이너 생성이 거부된다) 그 수로 줄인다."""
    reasons = []
    idx = cost.ORDER.index(tier)
    for i in range(idx, -1, -1):
        t = cost.ORDER[i]
        p = PROFILES[t]
        need = p["app_mem_mb"] + (p["db_mem_mb"] if use_database else 0)
        if need <= mem_mb * MEM_SHARE:
            if i < idx:
                reasons.append(f"Docker가 쓸 수 있는 메모리 {mem_mb}MB 안에서는 '{PROFILES[tier]['label']}' 단계를 만들 수 없어 낮춤")
            prof = dict(p)
            prof["app_cpus"] = min(p["app_cpus"], float(ncpu))
            prof["db_cpus"] = min(p["db_cpus"], float(ncpu))
            return t, prof, reasons
    need = PROFILES["lean"]["app_mem_mb"] + (PROFILES["lean"]["db_mem_mb"] if use_database else 0)
    raise LocalError("capacity", f"Docker가 쓸 수 있는 메모리({mem_mb}MB)가 가장 작은 구성의 한도({need}MB)의 {MEM_SHARE:.0%}에도 못 미칩니다. "
                                 "Docker Desktop의 메모리를 늘려 주세요")


def _q(s):
    """YAML 큰따옴표 문자열(JSON 문자열은 YAML에서도 같은 뜻이다)."""
    return json.dumps(s, ensure_ascii=False)


def _num(x):
    return format(x, "g")


def render_compose(p):
    """승인된 변수에서 compose 파일을 만든다. 같은 입력은 항상 같은 글자를 낸다(그 SHA-256이 승인 대상이다).

    p: deploy_id, image, container_port, host_port, bind, environment, use_database, app_cpus, app_mem_mb, db_cpus, db_mem_mb(, db_image).
    허용된 모양만 만든다: 호스트 경로 마운트·docker 소켓·특권 모드·네트워크 모드 변경이 없다. 형식이 틀린 입력은 ValueError."""
    deploy_id, image = p.get("deploy_id"), p.get("image")
    if not isinstance(deploy_id, str) or not ID_RE.match(deploy_id):
        raise ValueError(f"deploy_id가 올바르지 않습니다: {deploy_id!r}")
    if not isinstance(image, str) or not IMAGE_RE.match(image) or not image.startswith(f"pc-local/{deploy_id}:"):
        raise ValueError(f"이미지 이름이 올바르지 않습니다: {image!r}")
    ports = []
    for k in ("container_port", "host_port"):
        v = p.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 65535:
            raise ValueError(f"{k}가 올바르지 않습니다: {v!r}")
        ports.append(v)
    bind = p.get("bind")
    if not isinstance(bind, str) or not BIND_RE.match(bind) or any(int(x) > 255 for x in bind.split(".")):
        raise ValueError(f"bind 주소가 올바르지 않습니다: {bind!r}")
    env = p.get("environment") or {}
    if not isinstance(env, dict) or len(env) > 20:
        raise ValueError("environment는 20개 이하의 객체여야 합니다")
    for k, v in env.items():
        if not isinstance(k, str) or not ENV_KEY_RE.match(k) or k == "DATABASE_URL" or not isinstance(v, str):
            raise ValueError(f"환경 변수가 올바르지 않습니다: {k!r}")
    for k in ("app_cpus", "db_cpus"):
        v = p.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.01 <= v <= 64:
            raise ValueError(f"{k}가 올바르지 않습니다: {v!r}")
    for k in ("app_mem_mb", "db_mem_mb"):
        v = p.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not 64 <= v <= 1 << 20:
            raise ValueError(f"{k}가 올바르지 않습니다: {v!r}")
    use_db = p.get("use_database") is True
    db_image = p.get("db_image", DB_IMAGE)
    if db_image != DB_IMAGE:
        raise ValueError(f"DB 이미지는 {DB_IMAGE}만 허용합니다: {db_image!r}")

    out = [
        "# Paved Clouds 로컬 Docker 배포(자동 생성). 이 파일의 SHA-256이 사용자가 승인한 값이다. 직접 고치지 마세요.",
        "services:",
        "  app:",
        f"    image: {_q(image)}",
        "    pull_policy: never",
        "    restart: unless-stopped",
        f"    cpus: {_num(p['app_cpus'])}",
        f"    mem_limit: {_q(str(p['app_mem_mb']) + 'm')}",
        "    security_opt:",
        '      - "no-new-privileges:true"',
        "    ports:",
        f"      - {_q(f'{bind}:{ports[1]}:{ports[0]}')}",
    ]
    if env:
        out.append("    environment:")
        for k in sorted(env):
            out.append(f"      {k}: {_q(env[k].replace('$', '$$'))}")   # compose는 값의 $를 변수로 풀기 때문에 $$로 막는다
    if use_db:
        out += ["    env_file:", '      - "./secrets/app.env"', "    depends_on:", "      db:", "        condition: service_healthy"]
        name = f"app_{deploy_id}"
        out += [
            "  db:",
            f"    image: {_q(DB_IMAGE)}",
            "    restart: unless-stopped",
            f"    cpus: {_num(p['db_cpus'])}",
            f"    mem_limit: {_q(str(p['db_mem_mb']) + 'm')}",
            "    environment:",
            f"      MYSQL_DATABASE: {_q(name)}",
            f"      MYSQL_USER: {_q(name)}",
            "    env_file:",
            '      - "./secrets/db.env"',
            "    volumes:",
            '      - "db-data:/var/lib/mysql"',
            "    healthcheck:",
            '      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"]',
            "      interval: 5s",
            "      timeout: 5s",
            "      retries: 30",
            "      start_period: 20s",
            "volumes:",
            "  db-data: {}",
        ]
    return "\n".join(out) + "\n"


def compose_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def params_from_variables(v):
    """계획 변수(`make_plan`이 만든 것)에서 render_compose 입력을 읽는다. 형식이 틀리면 ValueError."""
    app, local = v.get("app"), v.get("local")
    if not isinstance(app, dict) or not isinstance(local, dict):
        raise ValueError("variables.app 또는 variables.local이 없습니다")
    return {
        "deploy_id": v.get("deploy_id"), "image": v.get("image"),
        "container_port": app.get("container_port"), "host_port": local.get("host_port"), "bind": local.get("bind"),
        "environment": app.get("environment") or {}, "use_database": app.get("use_database") is True,
        "app_cpus": local.get("app_cpus"), "app_mem_mb": local.get("app_mem_mb"),
        "db_cpus": local.get("db_cpus"), "db_mem_mb": local.get("db_mem_mb"),
    }


def public_url(v):
    """접속 주소. 변수에 적힌 주소 문자열을 믿지 않고 호스트 이름과 포트로 다시 만든다(백엔드로 보내는 URL이 http://이름:포트 모양임을 보장). 형식이 틀리면 ValueError."""
    local = v.get("local")
    host = local.get("public_host") if isinstance(local, dict) else None
    port = local.get("host_port") if isinstance(local, dict) else None
    if not isinstance(host, str) or not HOST_RE.match(host) or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("variables.local의 public_host 또는 host_port가 올바르지 않습니다")
    return f"http://{host}:{port}"


def make_plan(*, deploy_id, image, dockerfile, app, source_sha256, tier, profile, reasons, host_port, bind, public_host, docker_info,
              dockerfile_draft=None):
    """승인 화면에 보일 계획을 만든다. 반환: {"variables", "summary", "cost_estimate", "digest"}.

    cost_estimate는 0이다(추가 요금 없음). 전기·회선·PC 자원은 포함하지 않는다고 밝힌다(AGENTS.md 6장: 제외 비용을 총액에 섞지 않는다)."""
    use_db = bool(app.get("use_database"))
    params = {
        "deploy_id": deploy_id, "image": image, "container_port": app["container_port"], "host_port": host_port, "bind": bind,
        "environment": app.get("environment") or {}, "use_database": use_db,
        "app_cpus": profile["app_cpus"], "app_mem_mb": profile["app_mem_mb"], "db_cpus": profile["db_cpus"], "db_mem_mb": profile["db_mem_mb"],
    }
    compose_yaml = render_compose(params)
    digest = compose_digest(compose_yaml)
    url = f"http://{public_host}:{host_port}"
    resources = [{"service": "앱 컨테이너", "spec": f"CPU {_num(profile['app_cpus'])}, 메모리 {profile['app_mem_mb']}MB 한도, restart: unless-stopped",
                  "monthlyUsd": 0, "why": "이 PC의 Docker로 실행합니다. 호스트 포트 %d → 컨테이너 %d" % (host_port, app["container_port"])}]
    usage = f"CPU {_num(profile['app_cpus'])}, 메모리 {profile['app_mem_mb']}MB 한도"
    if use_db:
        resources.append({"service": "MySQL 컨테이너", "spec": f"{DB_IMAGE}, CPU {_num(profile['db_cpus'])}, 메모리 {profile['db_mem_mb']}MB 한도, 볼륨 유지",
                          "monthlyUsd": 0, "why": "앱 전용 DB와 계정을 이 배포 전용 컨테이너에 만듭니다. 비밀번호는 실행할 때 만들어 이 PC에만 둡니다."})
        usage += f" + DB CPU {_num(profile['db_cpus'])}, 메모리 {profile['db_mem_mb']}MB 한도"
    excluded = ["전기·회선·PC 사용 비용", "이 PC의 디스크 사용량", "HTTPS·도메인(로컬 주소로만 접속)"]
    variables = {
        "deploy_id": deploy_id, "image": image, "dockerfile": dockerfile, "app": app,
        # 소스에 Dockerfile이 없으면 분석기 초안 {content, sha256}. 계획 변수라 승인 대상이고, 빌드는 이 내용으로만 함
        "dockerfile_draft": dockerfile_draft,
        "source_sha256": source_sha256,
        "tier": tier, "recommended": True, "headline": profile["headline"], "tradeoff": profile["tradeoff"], "reason": ". ".join(reasons),
        "resources": resources, "usage_note": usage,
        "cost": {"app_monthly": 0, "app_peak_monthly": 0, "shared_monthly": 0, "total_monthly": 0, "peak_monthly": 0, "amount_basis": "app_peak_monthly", "excluded": excluded},
        "connection_name": CONNECTION_NAME,
        "local": {"project": f"pc-{deploy_id}", "host_port": host_port, "bind": bind, "public_host": public_host, "url": url,
                  "app_cpus": profile["app_cpus"], "app_mem_mb": profile["app_mem_mb"], "db_cpus": profile["db_cpus"], "db_mem_mb": profile["db_mem_mb"],
                  "docker_ncpu": docker_info["ncpu"], "docker_mem_mb": docker_info["mem_mb"]},
        "compose_yaml": compose_yaml,
    }
    summary = "\n".join([
        f"{profile['label']} 구성({tier}): 앱 컨테이너 1개{' + MySQL 컨테이너 1개' if use_db else ''}, 컨테이너 포트 {app['container_port']}, "
        f"헬스체크 {app['health_check_path']}, 앱 전용 DB {'사용' if use_db else '미사용'}",
        f"배포 위치: 이 PC의 Docker(플랫폼 worker를 실행하는 PC). 접속 주소 {url} (HTTP, 도메인·HTTPS 없음)",
        f"자원 한도: {usage}",
        "월 비용: 추가 비용 없음(전기·회선·PC 사용 비용은 포함하지 않음)",
        f"Docker가 쓸 수 있는 자원: CPU {docker_info['ncpu']}개, 메모리 {docker_info['mem_mb']}MB",
        f"선택 이유: {variables['reason']}",
        "승인하면 이 PC에서 소스를 이미지로 빌드하고 docker compose로 컨테이너를 띄웁니다. 승인한 compose 파일 외에는 실행하지 않습니다.",
    ])
    cost_estimate = {"amount": "0.0000", "currency": "USD", "period": "month", "pricing_as_of": "해당 없음(로컬 Docker)"}
    return {"variables": variables, "summary": summary, "cost_estimate": cost_estimate, "digest": digest}


# --- 하위 프로세스 ---------------------------------------------------------------------------------
def docker_env():
    """docker 명령에 넘길 환경. worker 토큰과 AWS 자격 증명은 넘기지 않는다(빌드·실행에 필요 없다)."""
    env = dict(os.environ)
    for k in ("WORKER_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        env.pop(k, None)
    return env


def _run(cmd, timeout, cwd=None, env=None):
    """(종료 코드, 표준 출력+표준 오류)를 돌려준다. 시간 초과면 프로세스를 종료하고 종료 코드 124와 그 사실을 남긴다. 실행 파일이 없으면 LocalError."""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, r.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + f"\n[worker] 제한 시간({timeout}초)을 넘겨 작업을 종료했습니다"
    except FileNotFoundError:
        raise LocalError("docker_missing", "docker 명령을 찾지 못했습니다. Docker Desktop이 설치돼 있고 PATH에 있는지 확인하세요") from None


def docker_info(lc):
    """Docker가 실행 중인지, compose v2가 있는지 확인하고 쓸 수 있는 자원을 읽는다. 반환: {"ncpu", "mem_mb", "version"}."""
    rc, out = _run([*lc.docker, "info", "--format", "{{.NCPU}} {{.MemTotal}} {{.ServerVersion}}"], 60, env=docker_env())
    parts = out.strip().splitlines()[-1].split() if out.strip() else []
    if rc != 0 or len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
        raise LocalError("docker_unavailable", "Docker 엔진에 연결하지 못했습니다. Docker Desktop을 실행했는지 확인하세요: " + redact(out.strip())[-200:])
    rc2, out2 = _run([*lc.docker, "compose", "version", "--short"], 60, env=docker_env())
    if rc2 != 0:
        raise LocalError("compose_missing", "docker compose(v2)를 쓸 수 없습니다. Docker Desktop 최신 버전이 필요합니다: " + redact(out2.strip())[-200:])
    return {"ncpu": int(parts[0]), "mem_mb": int(parts[1]) // (1024 * 1024), "version": parts[2]}


def deploy_dir(lc, deploy_id):
    if not ID_RE.match(deploy_id or ""):
        raise LocalError("invalid_job", f"deploy_id가 올바르지 않습니다: {deploy_id!r}")
    return Path(lc.root) / deploy_id


def _compose(lc, deploy_id, *args, timeout, cwd=None):
    d = deploy_dir(lc, deploy_id)
    cmd = [*lc.docker, "compose", "-p", f"pc-{deploy_id}", "-f", str(d / "docker-compose.yml"), *args]
    # 배포 폴더가 아직 없으면(status·destroy로 없는 ID를 조회) cwd로 쓸 수 없다. 이때는 docker가 compose 파일이 없다고 알리고 끝난다
    return _run(cmd, timeout, cwd=str(d) if d.is_dir() else None, env=docker_env())


# --- 소스 ZIP ---------------------------------------------------------------------------------------
def extract_zip(src, dst):
    """ZIP을 dst에 푼다. 경로 이탈(../, 절대 경로)·심볼릭 링크·암호 ZIP·과도한 크기·압축 폭탄은 LocalError("source_invalid").
    압축 안에 최상위 폴더가 하나뿐이면(GitHub 아카이브) 그 폴더를 기준으로 삼아 돌려준다. deploy.sh의 extract_zip과 같은 규칙이다."""
    max_bytes = int(os.environ.get("ZIP_MAX_BYTES", 1 << 30))
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    root = os.path.realpath(dst)

    def bad(msg):
        raise LocalError("source_invalid", msg)

    try:
        z = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as e:
        raise LocalError("source_invalid", f"소스 ZIP을 열지 못했습니다({type(e).__name__})") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > max_bytes:
            bad("ZIP이 너무 큽니다(파일 수 또는 풀었을 때 크기 한도 초과)")
        for i in infos:
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
                bad(f"ZIP에 허용되지 않는 경로가 있습니다: {name}")
            if i.flag_bits & 0x1:
                bad("암호가 걸린 ZIP은 지원하지 않습니다")
            if stat.S_ISLNK(i.external_attr >> 16):
                bad(f"ZIP에 심볼릭 링크가 있습니다: {name}")
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                bad(f"ZIP 경로가 대상 폴더 밖을 가리킵니다: {name}")
            if i.file_size > (1 << 20) and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                bad(f"압축률이 비정상적으로 높은 파일이 있습니다(압축 폭탄 의심): {name}")
        written = 0   # 헤더에 적힌 크기가 거짓이어도 한도를 지키려고 실제로 쓴 바이트를 센다
        for i in infos:
            name = i.filename.replace("\\", "/")
            target = os.path.join(root, name)
            if i.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(i) as src_f, open(target, "wb") as out_f:
                while True:
                    chunk = src_f.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        bad("ZIP이 너무 큽니다(풀었을 때 크기 한도 초과)")
                    out_f.write(chunk)
            # ZIP의 실행 비트만 더한다. 기본 권한(umask)을 유지하고 특권·쓰기 비트는 복원하지 않는다.
            executable = (i.external_attr >> 16) & 0o111
            if executable:
                os.chmod(target, stat.S_IMODE(os.stat(target).st_mode) | executable)
    entries = [e for e in os.listdir(root) if e != "__MACOSX"]
    one = os.path.join(root, entries[0]) if len(entries) == 1 else None
    return Path(one) if one and os.path.isdir(one) else Path(root)


def build_image(lc, zip_path, dockerfile, image, draft=None):
    """승인된 소스 ZIP에서 이미지를 만든다(이 PC의 Docker에만 남고 어디에도 올리지 않는다). 압축을 푼 임시 폴더는 항상 지운다.

    dockerfile 이 None 이면 승인된 Dockerfile 초안(draft {content, sha256})을 지문 확인 뒤 소스 밖 임시 파일로 써서 -f 로 쓴다.
    소스는 바꾸지 않는다."""
    if dockerfile is None:
        content = draft.get("content") if isinstance(draft, dict) else None
        expected = draft.get("sha256") if isinstance(draft, dict) else None
        if (not isinstance(content, str) or not isinstance(expected, str)
                or hashlib.sha256(content.encode("utf-8")).hexdigest() != expected):
            raise LocalError("draft_mismatch", "승인된 Dockerfile 초안을 확인하지 못했습니다(내용·지문 불일치). 빌드하지 않았습니다")
    elif (not isinstance(dockerfile, str) or not dockerfile or len(dockerfile) > 200
            or PureWindowsPath(dockerfile).anchor or ".." in dockerfile):
        raise LocalError("invalid_job", f"dockerfile 경로가 올바르지 않습니다: {dockerfile!r}")
    if not IMAGE_RE.match(image or ""):
        raise LocalError("invalid_job", f"이미지 이름이 올바르지 않습니다: {image!r}")
    work = Path(tempfile.mkdtemp(prefix="pc-local-build-"))
    try:
        ctx = extract_zip(zip_path, work / "src").resolve()
        if dockerfile is None:
            df = work / "draft" / "Dockerfile"   # 소스(ctx) 밖. 압축 푼 소스는 그대로 둔다
            df.parent.mkdir()
            df.write_text(draft["content"], encoding="utf-8", newline="\n")
        else:
            df = (ctx / dockerfile.replace("\\", "/")).resolve()
            if not df.is_relative_to(ctx):
                raise LocalError("invalid_job", f"dockerfile 경로가 소스 폴더 밖을 가리킵니다: {dockerfile!r}")
            if not df.is_file():
                raise LocalError("build_failed", f"Dockerfile을 찾지 못했습니다: {dockerfile}")
        env = docker_env()
        _run([*lc.docker, "rmi", "-f", image], 120, env=env)   # 같은 태그의 옛 이미지가 남아 빌드 실패 뒤에 쓰이지 않게 먼저 지운다
        rc, out = _run([*lc.docker, "build", "--progress=plain", "-f", str(df), "-t", image, str(ctx)], lc.build_timeout, env=env)
        if rc != 0:
            raise LocalError("build_failed", "이미지를 빌드하지 못했습니다", {"log_tail": tail(out)})
        rc, out = _run([*lc.docker, "image", "inspect", "--format", "{{.Id}}", image], 60, env=env)
        if rc != 0:
            raise LocalError("build_failed", "빌드는 끝났지만 이미지를 찾지 못했습니다", {"log_tail": tail(out)})
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- 적용 ---------------------------------------------------------------------------------------------
def _write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def volume_exists(lc, deploy_id):
    rc, _ = _run([*lc.docker, "volume", "inspect", f"pc-{deploy_id}_db-data"], 60, env=docker_env())
    return rc == 0


def ensure_secrets(lc, deploy_id, use_database):
    """DB 비밀번호를 만들어 secrets/*.env에 둔다(소유자만 읽도록 0600, Windows는 사용자 폴더 권한을 따른다).

    이미 있으면 다시 만들지 않는다: MySQL은 첫 초기화 때 비밀번호가 정해져서, 볼륨은 남기고 비밀번호만 바꾸면 앱이 DB에 접속하지 못한다.
    비밀 파일 없이 볼륨만 남은 상태는 임의로 이어 쓰지 않고 실패로 알린다(데이터를 지우는 것은 사람이 정한다)."""
    d = deploy_dir(lc, deploy_id) / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    if not use_database:
        return
    db_env, app_env = d / "db.env", d / "app.env"
    if db_env.is_file() and app_env.is_file():
        return
    if volume_exists(lc, deploy_id):
        raise LocalError("secrets_missing", f"이 배포의 DB 볼륨(pc-{deploy_id}_db-data)이 남아 있는데 비밀 파일이 없습니다. 데이터를 지워도 되면 "
                                            f"`python infra/worker/local_docker.py destroy {deploy_id} --drop-data`로 정리한 뒤 다시 배포하세요")
    name = f"app_{deploy_id}"
    root_pw, app_pw = secrets.token_urlsafe(24), secrets.token_urlsafe(24)   # URL에 넣어도 인코딩이 필요 없는 문자(A-Za-z0-9-_)만 나온다
    _write_private(db_env, f"MYSQL_ROOT_PASSWORD={root_pw}\nMYSQL_PASSWORD={app_pw}\n")
    _write_private(app_env, f"DATABASE_URL=mysql://{name}:{app_pw}@db:3306/{name}\n")


def known_secrets(lc, deploy_id):
    """secrets/*.env의 값들. 로그에서 모양과 상관없이 가리는 데 쓴다."""
    vals = []
    d = deploy_dir(lc, deploy_id) / "secrets"
    for f in ("db.env", "app.env"):
        try:
            for line in (d / f).read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    vals.append(line.split("=", 1)[1].strip())
        except OSError:
            pass
    return vals


def service_states(lc, deploy_id):
    """{서비스: (상태, 종료 코드)}. `docker compose ps -a --format json`은 버전에 따라 한 줄씩 또는 배열로 낸다. 읽지 못하면 빈 딕셔너리."""
    rc, out = _compose(lc, deploy_id, "ps", "-a", "--format", "json", timeout=60)
    if rc != 0:
        return {}
    items = []
    for chunk in ([out.strip()] if out.strip().startswith("[") else out.splitlines()):
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        items.extend(data if isinstance(data, list) else [data])
    states = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("Service"), str):
            states[it["Service"]] = (str(it.get("State", "")), it.get("ExitCode"))
    return states


def diagnose(lc, deploy_id):
    """실패 분석용 정보(컨테이너 상태와 최근 로그). 비밀은 가린다."""
    known = known_secrets(lc, deploy_id)
    states = service_states(lc, deploy_id)
    rc, out = _compose(lc, deploy_id, "logs", "--no-color", "--tail", "40", timeout=60)
    return {"deployId": deploy_id, "services": [{"service": s, "state": st, "exitCode": code} for s, (st, code) in sorted(states.items())],
            "log_tail": tail(out, 60, known) if rc == 0 else ""}


def _stop(lc, deploy_id):
    """실패한 배포의 컨테이너를 멈춘다(지우지 않는다: 로그와 상태를 볼 수 있게. restart: unless-stopped의 재시작 루프가 PC 자원을 쓰지 않게)."""
    _compose(lc, deploy_id, "stop", timeout=120)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, timeout=3):
    """헬스체크 요청 한 번. 응답 코드를 돌려주고 연결하지 못하면 None. 리다이렉트는 따르지 않는다(3xx도 앱이 응답한 것이다)."""
    try:
        with urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def wait_healthy(lc, deploy_id, host_port, health_path, *, bind=None):
    """앱이 헬스체크에 응답할 때까지 기다린다. AWS 경로(infra/README.md)와 같은 판정이다.

    통과: 응답 코드 200~399. 실패: 4xx가 4번 연속(기다려도 나아지지 않는다), 앱 컨테이너가 종료(exited·dead)되거나 재시작을 3번 연속 확인,
    제한 시간(health_timeout) 초과. 5xx와 연결 실패는 시작 중일 수 있어 계속 기다린다."""
    bind = lc.bind if bind is None else bind
    host = "127.0.0.1" if bind == "0.0.0.0" else bind
    url = f"http://{host}:{host_port}{health_path}"
    deadline = time.monotonic() + lc.health_timeout
    four_xx = restarting = 0
    last = None
    while True:
        code = probe(url)
        last = code
        if code is not None and 200 <= code < 400:
            return
        four_xx = four_xx + 1 if code is not None and 400 <= code < 500 else 0
        if four_xx >= 4:
            raise LocalError("health_failed", f"헬스체크 {health_path}가 4xx({code})를 연속으로 돌려줬습니다. 경로를 확인하세요", {"diagnose": diagnose(lc, deploy_id)})
        st = service_states(lc, deploy_id).get("app")
        if st:
            state, exit_code = st
            if state in ("exited", "dead"):
                raise LocalError("container_exited", f"앱 컨테이너가 종료됐습니다(종료 코드 {exit_code})", {"diagnose": diagnose(lc, deploy_id)})
            restarting = restarting + 1 if state == "restarting" else 0
            if restarting >= 3:
                raise LocalError("container_restarting", "앱 컨테이너가 계속 다시 시작되고 있습니다", {"diagnose": diagnose(lc, deploy_id)})
        if time.monotonic() >= deadline:
            raise LocalError("health_timeout", f"{lc.health_timeout}초 안에 헬스체크를 통과하지 못했습니다(마지막 응답: {last if last is not None else '연결 실패'})",
                             {"diagnose": diagnose(lc, deploy_id)})
        time.sleep(lc.poll_seconds)


PORT_BUSY_RE = re.compile(r"(?i)port is already allocated|address already in use|bind for .* failed|ports are not available")


def apply(lc, deploy_id, compose_text, *, host_port, health_path, bind=None, init_command=None, use_database=False, on_deploying=None):
    """승인된 compose 파일을 이 PC의 Docker로 올리고 헬스체크를 통과할 때까지 기다린다. 실패는 LocalError이고, 이때 컨테이너는 멈춘다."""
    d = deploy_dir(lc, deploy_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker-compose.yml").write_text(compose_text, encoding="utf-8", newline="\n")
    ensure_secrets(lc, deploy_id, use_database)
    known = known_secrets(lc, deploy_id)
    try:
        rc, out = _compose(lc, deploy_id, "up", "-d", "--remove-orphans", "--quiet-pull", timeout=lc.up_timeout)
        if rc != 0:
            msg = (f"호스트 포트 {host_port}가 이미 사용 중입니다. 다른 프로그램이나 다른 배포가 쓰고 있는지 확인하세요" if PORT_BUSY_RE.search(out)
                   else "docker compose up이 실패했습니다")
            raise LocalError("apply_failed", msg, {"log_tail": tail(out, 40, known)})
        if init_command:
            name = f"pc-{deploy_id}-init"
            _run([*lc.docker, "rm", "-f", name], 60, env=docker_env())
            rc, out = _compose(lc, deploy_id, "run", "--rm", "-T", "--no-deps", "--name", name, "app", *init_command, timeout=lc.init_timeout)
            if rc != 0:
                _run([*lc.docker, "rm", "-f", name], 60, env=docker_env())   # 시간 초과로 끊긴 초기화 컨테이너가 남지 않게 한다
                why = "제한 시간 초과" if rc == 124 else f"종료 코드 {rc}"
                raise LocalError("init_failed", f"앱 초기화 작업이 실패했습니다({why})", {"log_tail": tail(out, 40, known)})
        if on_deploying:
            on_deploying()
        wait_healthy(lc, deploy_id, host_port, health_path, bind=bind)
    except LocalError as e:
        if e.event_type not in ("docker_missing",):
            _stop(lc, deploy_id)
        if e.details is None:
            e.details = {"diagnose": diagnose(lc, deploy_id)}
        raise
    with open(d / "history.log", "a", encoding="utf-8") as f:   # 헬스체크를 통과한 배포만(AWS의 history.log와 같은 뜻)
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}|{compose_digest(compose_text)}\n")


# --- 운영자 정리 --------------------------------------------------------------------------------------
def list_deployments(lc):
    root = Path(lc.root)
    return sorted(p.name for p in root.iterdir() if p.is_dir() and ID_RE.match(p.name)) if root.is_dir() else []


def destroy(lc, deploy_id, drop_data=False):
    """컨테이너와 네트워크를 지운다. drop_data면 DB 볼륨과 배포 폴더(비밀 파일 포함)까지 지운다(되돌릴 수 없다)."""
    d = deploy_dir(lc, deploy_id)
    if not (d / "docker-compose.yml").is_file():
        raise LocalError("not_found", f"배포 {deploy_id}의 compose 파일이 없습니다: {d}")
    args = ["down", "--remove-orphans"] + (["--volumes"] if drop_data else [])
    rc, out = _compose(lc, deploy_id, *args, timeout=300)
    if rc != 0:
        raise LocalError("destroy_failed", "docker compose down이 실패했습니다", {"log_tail": tail(out)})
    if drop_data:
        shutil.rmtree(d, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.environ.get("LOCAL_DOCKER_DIR", str(INFRA / "local-deployments")))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status").add_argument("id")
    d = sub.add_parser("destroy")
    d.add_argument("id")
    d.add_argument("--drop-data", action="store_true")
    a = p.parse_args(argv)
    lc = LocalConfig(root=Path(a.root))
    try:
        if a.cmd == "list":
            print("\n".join(list_deployments(lc)))
        elif a.cmd == "status":
            print(json.dumps(diagnose(lc, a.id), ensure_ascii=False, indent=2))
        else:
            destroy(lc, a.id, a.drop_data)
            print(f"배포 {a.id}를 지웠습니다" + (" (DB 볼륨과 비밀 파일 포함)" if a.drop_data else " (DB 볼륨과 비밀 파일은 남겼습니다)"))
    except LocalError as e:
        print(f"오류: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
