"""코드 분석 (규칙 기반).

LLM 없이 파일에서 확실히 찾을 수 있는 것부터 찾는다. 찾은 값마다 근거(파일:줄)를 남긴다.
못 찾은 값은 추측하지 않고 unresolved에 이유를 남긴다 → 다음 단계에서 LLM이 채우거나 화면에 이유를 보여 준다.

결과(to_result)는 그대로 POST /api/projects/{id}/analyses 의 result가 된다.
- 프런트는 stack·findings·evidence를 읽는다 (front/README.md)
- 인프라 worker(infra/worker, planner)는 app_config·dockerfile·scale을 읽어 계획을 만든다 (infra/worker/README.md "분석 결과의 계약")
  app_config는 infra/modules/ecs-web-app/app-config.schema.json 의 분석 값(포트·헬스체크·DB·환경 변수·init_command)과 같은 규칙이다.
  task_size·min_tasks·max_tasks는 worker가 구성 단계로 정하므로 넣지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .dockerfile import draft_dockerfile
from .masking import MaskReport, backend_safe_text, mask_files
from .source import SourceFile

SCHEMA_VERSION = "analysis/1"

# app-config.schema.json 의 environment 규칙
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
ENV_FORBIDDEN = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE|CREDENTIAL|API_?KEY|ACCESS_?KEY)")
ENV_MAX = 20
# 배포가 따로 넣는 변수라 environment에 넣지 않음
ENV_MANAGED = {"DATABASE_URL", "PORT"}
_PLACEHOLDER = re.compile(r"(?i)^(replace[-_ ]?me|change[-_ ]?me|todo|xxx+|your[-_].*|<.*>|\$\{.*\}|example)$")

HEALTH_PATHS = ["/health", "/healthz", "/health-check", "/healthcheck", "/api/health", "/ping", "/status", "/ready"]


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    text: str

    def as_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "text": backend_safe_text(self.text.strip())[:160]}


@dataclass
class Finding:
    level: str  # "info" | "warn"
    title: str
    detail: str


@dataclass
class Analysis:
    supported: bool = True
    unsupported_reasons: List[str] = field(default_factory=list)
    framework: Optional[str] = None
    runtime: Optional[str] = None
    database: Optional[str] = None
    has_dockerfile: bool = False
    container_port: Optional[int] = None
    health_check_path: Optional[str] = None
    use_database: Optional[bool] = None
    environment: Dict[str, str] = field(default_factory=dict)
    # 배포 뒤 앱 이미지로 한 번 실행할 초기화 명령 (테이블 생성 등). 없으면 생략
    init_command: Optional[List[str]] = None
    # 소스 안 Dockerfile 상대 경로
    dockerfile: Optional[str] = None
    sizing_hints: Dict[str, object] = field(default_factory=dict)
    unresolved: Dict[str, str] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    withheld_files: List[str] = field(default_factory=list)
    redactions: int = 0
    # LLM이 채운 값 (규칙이 못 찾은 것만). 화면에서 근거 확인을 권함
    ai_filled: List[str] = field(default_factory=list)
    ai_model: Optional[str] = None
    # Dockerfile이 없을 때 만든 초안 {path, content, port, based_on}. 사용자가 저장소에 넣고 다시 올리는 제안
    dockerfile_draft: Optional[dict] = None

    def warn(self, title: str, detail: str) -> None:
        self.findings.append(Finding("warn", title, detail))

    def info(self, title: str, detail: str) -> None:
        self.findings.append(Finding("info", title, detail))

    def app_config(self) -> dict:
        """worker가 읽는 앱 설정. 못 찾은 값이 있으면 None으로 두고 unresolved에 이유가 있음 → worker 검증에서 걸러짐"""
        cfg: dict = {
            "container_port": self.container_port,
            "health_check_path": self.health_check_path,
            "use_database": self.use_database,
            "environment": self.environment,
        }
        if self.init_command:
            cfg["init_command"] = self.init_command
        return cfg

    def to_result(self, scale: Optional[dict] = None) -> dict:
        """백엔드에 기록할 분석 결과.

        scale은 사용자가 입력한 사용 규모·예산 {expected_users, traffic_pattern, monthly_budget_usd}.
        백엔드 API에 받는 곳이 없어 분석 결과에 담기로 함 (infra/worker/README.md). 모르면 None → worker는 권장 단계, 예산 검사 안 함
        """
        stack = [
            ("프레임워크", self.framework or "확인 안 됨"),
            ("런타임", self.runtime or "확인 안 됨"),
            ("포트", str(self.container_port) if self.container_port else "확인 안 됨"),
            ("DB", self.database or ("사용" if self.use_database else "사용 안 함" if self.use_database is False else "확인 안 됨")),
            ("헬스체크", self.health_check_path or "확인 안 됨"),
            ("환경 변수", f"{len(self.environment)}개" if self.environment else "없음"),
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "supported": self.supported,
            "unsupported_reasons": self.unsupported_reasons,
            "stack": [{"label": k, "value": v} for k, v in stack],
            "findings": [{"level": f.level, "title": f.title, "detail": backend_safe_text(f.detail)} for f in self.findings],
            "evidence": [e.as_dict() for e in self.evidence],
            # 미지원 앱은 app_config 를 주지 않아 worker가 계획을 만들지 못하게 함 (PR #25 리뷰: PostgreSQL 앱이 MySQL로 배포됐음)
            "app_config": self.app_config() if self.supported else None,
            "dockerfile": self.dockerfile,
            "scale": scale,
            "unresolved": self.unresolved,
            "sizing_hints": self.sizing_hints,
            "has_dockerfile": self.has_dockerfile,
            "dockerfile_draft": (
                {**self.dockerfile_draft, "based_on": [backend_safe_text(b) for b in self.dockerfile_draft["based_on"]]}
                if self.dockerfile_draft else None
            ),
            "masking": {"withheld_files": self.withheld_files, "redactions": self.redactions},
            "ai": {"model": self.ai_model, "filled": self.ai_filled},
        }


# ---------- 파일 도우미 ----------


class Repo:
    def __init__(self, files: List[SourceFile]):
        self.files = files
        self.by_path = {f.path: f for f in files}

    def named(self, *names: str) -> List[SourceFile]:
        """파일 이름(경로 끝)이 일치하는 파일. 루트에 가까운 것부터"""
        lower = {n.lower() for n in names}
        hits = [f for f in self.files if f.path.rsplit("/", 1)[-1].lower() in lower]
        return sorted(hits, key=lambda f: (f.path.count("/"), f.path))

    def code(self, *exts: str) -> List[SourceFile]:
        return [f for f in self.files if f.path.lower().endswith(exts)]

    @staticmethod
    def grep(f: SourceFile, pattern: re.Pattern) -> List[Tuple[int, re.Match]]:
        hits = []
        for i, line in enumerate(f.text.splitlines(), 1):
            m = pattern.search(line)
            if m:
                hits.append((i, m))
        return hits


def _line_of(f: SourceFile, needle: str) -> Tuple[int, str]:
    for i, line in enumerate(f.text.splitlines(), 1):
        if needle.lower() in line.lower():
            return i, line
    return 1, needle


# ---------- 의존성 ----------


def _python_deps(repo: Repo) -> Dict[str, Tuple[SourceFile, int, str]]:
    deps: Dict[str, Tuple[SourceFile, int, str]] = {}
    for f in repo.named("requirements.txt", "requirements.in", "pyproject.toml", "Pipfile", "setup.py"):
        for i, line in enumerate(f.text.splitlines(), 1):
            for m in re.finditer(r"(?i)(?:^|[\"'\s,\[])([A-Za-z][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?\s*(?:[=<>~!]=|>|<|\"|'|,|\]|$)", line.strip()):
                name = m.group(1).lower().replace("_", "-")
                if name not in deps and not line.strip().startswith("#"):
                    deps[name] = (f, i, line)
    return deps


def _node_deps(repo: Repo) -> Dict[str, Tuple[SourceFile, int, str]]:
    deps: Dict[str, Tuple[SourceFile, int, str]] = {}
    for f in repo.named("package.json"):
        try:
            pkg = json.loads(f.text)
        except ValueError:
            continue
        for section in ("dependencies", "devDependencies"):
            for name in (pkg.get(section) or {}):
                i, line = _line_of(f, f'"{name}"')
                deps.setdefault(name.lower(), (f, i, line))
    return deps


def _jvm_deps(repo: Repo) -> Dict[str, Tuple[SourceFile, int, str]]:
    deps: Dict[str, Tuple[SourceFile, int, str]] = {}
    for f in repo.named("pom.xml", "build.gradle", "build.gradle.kts"):
        for key in ("spring-boot", "mysql-connector", "postgresql", "h2"):
            if key in f.text:
                i, line = _line_of(f, key)
                deps.setdefault(key, (f, i, line))
    return deps


# ---------- 판단 규칙 ----------

PY_FRAMEWORKS = [("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask"), ("starlette", "Starlette")]
NODE_FRAMEWORKS = [("next", "Next.js"), ("@nestjs/core", "NestJS"), ("express", "Express"), ("fastify", "Fastify"), ("koa", "Koa"), ("hono", "Hono")]

MYSQL_CLIENTS = {"pymysql", "mysqlclient", "mysql-connector-python", "aiomysql", "asyncmy", "mysql2", "mysql", "mysql-connector"}
ORMS = {"sqlalchemy", "sqlmodel", "tortoise-orm", "peewee", "prisma", "@prisma/client", "sequelize", "typeorm", "knex", "drizzle-orm"}
POSTGRES_CLIENTS = {"psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "pg", "postgres", "postgresql"}
MONGO_CLIENTS = {"pymongo", "motor", "mongoose", "mongodb"}
MEMORY_HEAVY = {"torch", "tensorflow", "transformers", "sentence-transformers", "opencv-python", "puppeteer", "playwright"}
WORKERS = {"celery", "rq", "dramatiq", "apscheduler", "bull", "bullmq", "node-cron", "agenda"}


def _detect_framework(a: Analysis, py: dict, node: dict, jvm: dict) -> Optional[str]:
    for dep, name in PY_FRAMEWORKS:
        if dep in py:
            f, i, line = py[dep]
            a.framework, family = name, "python"
            a.evidence.append(Evidence(f.path, i, line))
            return family
    for dep, name in NODE_FRAMEWORKS:
        if dep in node:
            f, i, line = node[dep]
            a.framework, family = name, "node"
            a.evidence.append(Evidence(f.path, i, line))
            return family
    if "spring-boot" in jvm:
        f, i, line = jvm["spring-boot"]
        a.framework = "Spring Boot"
        a.evidence.append(Evidence(f.path, i, line))
        return "jvm"
    return None


def _detect_runtime(a: Analysis, repo: Repo, family: Optional[str]) -> None:
    for f in repo.named("Dockerfile"):
        for i, m in repo.grep(f, re.compile(r"(?i)^\s*FROM\s+(?:--platform=\S+\s+)?([\w./-]+):?([\w.-]*)")):
            image, tag = m.group(1).rsplit("/", 1)[-1], m.group(2)
            ver = re.match(r"\d+(?:\.\d+)?", tag)
            names = {"python": "Python", "node": "Node.js", "eclipse-temurin": "Java", "openjdk": "Java", "golang": "Go"}
            if image in names:
                a.runtime = f"{names[image]} {ver.group(0)}" if ver else names[image]
                a.evidence.append(Evidence(f.path, i, m.group(0)))
                return
    for f in repo.named(".python-version", "runtime.txt", ".nvmrc"):
        v = f.text.strip().splitlines()[0] if f.text.strip() else ""
        if v:
            a.runtime = ("Node.js " if f.path.endswith(".nvmrc") else "Python ") + v.replace("python-", "")
            a.evidence.append(Evidence(f.path, 1, v))
            return
    if family:
        a.runtime = {"python": "Python", "node": "Node.js", "jvm": "Java"}[family]


_PORT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Dockerfile", re.compile(r"(?i)^\s*EXPOSE\s+(\d{2,5})")),
    ("Dockerfile", re.compile(r"(?i)^\s*(?:CMD|ENTRYPOINT).*?(?:--port[\"',\s=]+|-p[\"',\s]+|--bind[\"',\s=]+[\w.]*:|:)(\d{2,5})\b")),
    ("code", re.compile(r"uvicorn\.run\(.*?port\s*=\s*(\d{2,5})")),
    ("code", re.compile(r"\.run\(.*?port\s*=\s*(\d{2,5})")),
    ("code", re.compile(r"\.listen\(\s*(?:process\.env\.PORT\s*(?:\|\||\?\?)\s*)?(\d{2,5})")),
    ("code", re.compile(r"process\.env\.PORT\s*(?:\|\||\?\?)\s*(\d{2,5})")),
    ("env", re.compile(r"^\s*PORT\s*=\s*(\d{2,5})\s*$")),
    ("config", re.compile(r"(?i)^\s*server\.port\s*[=:]\s*(\d{2,5})")),
]


def _detect_port(a: Analysis, repo: Repo) -> None:
    sources: Dict[str, Callable[[], List[SourceFile]]] = {
        "Dockerfile": lambda: repo.named("Dockerfile"),
        "code": lambda: repo.code(".py", ".js", ".ts", ".mjs", ".cjs"),
        "env": lambda: repo.named(".env.example", ".env.sample", ".env.template"),
        "config": lambda: repo.named("application.properties", "application.yml", "application.yaml"),
    }
    found: List[Tuple[int, Evidence]] = []
    for kind, pattern in _PORT_PATTERNS:
        for f in sources[kind]():
            for i, m in repo.grep(f, pattern):
                port = int(m.group(1))
                if 1 <= port <= 65535:
                    found.append((port, Evidence(f.path, i, m.group(0))))
    if not found:
        a.unresolved["container_port"] = "Dockerfile·실행 코드·.env.example에서 포트를 찾지 못했습니다."
        return
    # 맨 앞 규칙(Dockerfile EXPOSE)이 가장 확실
    port, ev = found[0]
    a.container_port = port
    a.evidence.append(ev)
    others = sorted({p for p, _ in found if p != port})
    if others:
        a.warn("포트가 여러 곳에 다르게 적혀 있습니다", f"{ev.file}의 {port}번을 씁니다. 다른 값: {', '.join(map(str, others))}")


_ROUTE = re.compile(
    r"""(?x)
    @(?P<obj1>\w+)\.(?:get|route|api_route)\(\s*["'](?P<p1>[^"']+)["']        # FastAPI·Flask
    | \b(?P<obj2>app|router|server|\w*[Rr]outer)\.(?:get|all|head)\(\s*["'`](?P<p2>[^"'`]+)["'`]  # Express·Fastify
    | \bpath\(\s*["'](?P<p3>[^"']*)["']                                         # Django
    | @GetMapping\(\s*(?:value\s*=\s*)?["'](?P<p4>[^"']+)["']                   # Spring
    """
)
# 앱 객체에 바로 붙은 경로. 이 이름이 아니면 라우터로 봄
_APP_OBJECTS = {"app", "application", "server", "api"}
# 같은 파일에서 라우터를 만들며 정한 접두사: FastAPI APIRouter(prefix=), Flask Blueprint(url_prefix=)
_ROUTER_PREFIX = re.compile(r"""(\w+)\s*=\s*(?:APIRouter|Blueprint)\([^)]*?(?:url_)?prefix\s*=\s*["']([^"']*)["']""")
# 스프링 컨트롤러 클래스의 공통 경로
_SPRING_PREFIX = re.compile(r"""@RequestMapping\(\s*(?:value\s*=\s*|path\s*=\s*)?["']([^"']+)["']""")
# 다른 곳에서 접두사를 붙여 라우터를 마운트함 → 라우터에 적힌 경로만으로는 최종 경로를 모름
_MOUNT_PREFIX = re.compile(
    r"""include_router\([^)]*prefix\s*=|register_blueprint\([^)]*url_prefix\s*=|\.use\(\s*["'`]/[^"'`]*["'`]\s*,"""
)


def _join(prefix: str, path: str) -> str:
    joined = "/".join(p.strip("/") for p in (prefix, path) if p.strip("/"))
    return "/" + joined if joined else "/"


def _detect_health(a: Analysis, repo: Repo) -> None:
    sources = repo.code(".py", ".js", ".ts", ".mjs", ".cjs", ".java", ".kt")
    mounted = any(repo.grep(f, _MOUNT_PREFIX) for f in sources)
    routes: List[Tuple[str, Evidence]] = []
    unsure = False  # 라우터 경로라 최종 경로를 확정하지 못한 것이 있음
    for f in sources:
        prefixes = {name: prefix for name, prefix in _ROUTER_PREFIX.findall(f.text)}
        spring = _SPRING_PREFIX.search(f.text)
        for i, m in repo.grep(f, _ROUTE):
            obj = m.group("obj1") or m.group("obj2")
            path = next(m.group(g) for g in ("p1", "p2", "p3", "p4") if m.group(g) is not None)
            if m.group("p4") is not None and spring:
                path = _join(spring.group(1), path)
            elif obj and obj not in _APP_OBJECTS:
                # 라우터에 붙은 경로 (PR #25 리뷰: APIRouter(prefix="/api") 아래 /health 를 /health 로 확정했음)
                if mounted:
                    unsure = True
                    continue
                path = _join(prefixes.get(obj, ""), path)
            routes.append((_join("", path), Evidence(f.path, i, m.group(0))))
    for want in HEALTH_PATHS:
        for path, ev in routes:
            if path == want:
                a.health_check_path = path
                a.evidence.append(ev)
                return
    root = next(((p, ev) for p, ev in routes if p == "/"), None)
    if root and not unsure:
        a.health_check_path = "/"
        a.evidence.append(root[1])
        a.info("헬스체크 전용 경로가 없어 / 를 씁니다", "응답이 느린 첫 화면이면 /health 같은 가벼운 경로를 추가하는 게 좋습니다.")
        return
    if unsure:
        a.unresolved["health_check_path"] = "라우터가 다른 경로 아래에 붙어 있어 헬스체크의 전체 경로를 확정하지 못했습니다."
        return
    a.unresolved["health_check_path"] = "헬스체크로 쓸 경로(/health 또는 /)를 찾지 못했습니다."


# ORM·DATABASE_URL 만으로는 DB 종류를 모름. 주소 형식(postgresql://)·Prisma provider·ORM dialect 로 종류를 찾음
_DB_KIND = [
    ("PostgreSQL", re.compile(r"(?i)\bpostgres(?:ql)?(?:\+\w+)?://|provider\s*=\s*\"(?:postgresql|cockroachdb)\"|(?:dialect|type)\s*:\s*['\"]postgres")),
    ("MongoDB", re.compile(r"(?i)\bmongodb(?:\+srv)?://|provider\s*=\s*\"mongodb\"")),
    ("SQLite", re.compile(r"(?i)\bsqlite:|provider\s*=\s*\"sqlite\"|(?:dialect|type)\s*:\s*['\"](?:better-)?sqlite")),
    ("MySQL", re.compile(r"(?i)\b(?:mysql(?:\+\w+)?|mariadb)://|provider\s*=\s*\"mysql\"|(?:dialect|type)\s*:\s*['\"](?:mysql|mariadb)")),
]
_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _db_kind(repo: Repo) -> Optional[Tuple[str, SourceFile, int]]:
    """(DB 종류, 파일, 줄) 또는 None. 여러 종류가 보이면 None (단정하지 않음)"""
    files = repo.code(".py", ".js", ".ts", ".mjs", ".cjs", ".java", ".prisma") + repo.named(
        ".env.example", ".env.sample", ".env.template", *_COMPOSE_NAMES)
    found: Dict[str, Tuple[SourceFile, int]] = {}
    for f in files:
        for kind, pattern in _DB_KIND:
            hits = repo.grep(f, pattern)
            if hits and kind not in found:
                found[kind] = (f, hits[0][0])
    if len(found) != 1:
        return None
    kind, (f, line) = next(iter(found.items()))
    return kind, f, line


def _detect_database(a: Analysis, repo: Repo, deps: Dict[str, Tuple[SourceFile, int, str]], jvm: dict) -> None:
    def ev(name: str) -> None:
        f, i, line = deps[name]
        a.evidence.append(Evidence(f.path, i, line))

    mysql = [d for d in deps if d in MYSQL_CLIENTS] + (["mysql-connector"] if "mysql-connector" in jvm else [])
    orm = [d for d in deps if d in ORMS]
    pg = [d for d in deps if d in POSTGRES_CLIENTS] + (["postgresql"] if "postgresql" in jvm else [])
    mongo = [d for d in deps if d in MONGO_CLIENTS]

    sqlite_hits = []
    db_url_hits = []
    for f in repo.code(".py", ".js", ".ts", ".mjs", ".cjs", ".java"):
        sqlite_hits += [(f, i, m) for i, m in repo.grep(f, re.compile(r"sqlite3\.connect|sqlite:///|better-sqlite3|new sqlite3\.Database"))]
        db_url_hits += [(f, i, m) for i, m in repo.grep(f, re.compile(r"""DATABASE_URL"""))]
    for f in repo.named(".env.example", ".env.sample", ".env.template"):
        db_url_hits += [(f, i, m) for i, m in repo.grep(f, re.compile(r"^\s*DATABASE_URL\s*="))]

    if mongo and not (mysql or orm):
        a.supported = False
        a.unsupported_reasons.append("MongoDB를 쓰는 앱은 아직 지원하지 않습니다. 공용 DB는 MySQL입니다.")
        a.database, a.use_database = "MongoDB", True
        ev(mongo[0])
        return
    if pg and not mysql:
        a.supported = False
        a.unsupported_reasons.append("PostgreSQL 전용 드라이버를 씁니다. 공용 DB는 MySQL이라 드라이버를 바꿔야 배포할 수 있습니다.")
        a.database, a.use_database = "PostgreSQL", True
        if pg[0] in deps:
            ev(pg[0])
        return
    # ORM·DATABASE_URL 만 있으면 종류를 확인한 뒤에만 MySQL로 봄 (PR #25 리뷰: Prisma provider가 PostgreSQL이어도 MySQL로 배포됐음)
    kind = _db_kind(repo) if not mysql and (orm or db_url_hits) else None
    if kind and kind[0] in ("PostgreSQL", "MongoDB"):
        name, f, i = kind
        a.supported = False
        a.unsupported_reasons.append(f"{name}를 쓰는 앱은 아직 지원하지 않습니다. 공용 DB는 MySQL입니다.")
        a.database, a.use_database = name, True
        a.evidence.append(Evidence(f.path, i, f.text.splitlines()[i - 1]))
        return
    if mysql or (kind and kind[0] == "MySQL"):
        a.use_database, a.database = True, "MySQL"
        if mysql and mysql[0] in deps:
            ev(mysql[0])
        if kind:
            a.evidence.append(Evidence(kind[1].path, kind[2], kind[1].text.splitlines()[kind[2] - 1]))
        elif db_url_hits:
            f, i, m = db_url_hits[0]
            a.evidence.append(Evidence(f.path, i, f.text.splitlines()[i - 1]))
        a.info("DB 접속 정보는 배포가 넣습니다", "공용 MySQL에 앱 전용 DB와 계정을 만들고 DATABASE_URL을 비밀 저장소로 주입합니다.")
        return
    if (orm or db_url_hits) and not sqlite_hits and not (kind and kind[0] == "SQLite"):
        # DB는 쓰는데 종류를 모름 → MySQL 접속 정보를 넣어도 되는지 알 수 없어 배포하지 않음
        f, i, m = db_url_hits[0] if db_url_hits else (None, 0, None)
        if f:
            a.evidence.append(Evidence(f.path, i, f.text.splitlines()[i - 1]))
        a.use_database, a.database = True, "확인 안 됨"
        a.supported = False
        a.unsupported_reasons.append(
            "DB를 쓰지만 종류(MySQL·PostgreSQL 등)를 코드에서 확인하지 못했습니다. "
            ".env.example 의 DATABASE_URL 예시(mysql://...)나 ORM 설정에 DB 종류를 적어 주세요.")
        return
    if sqlite_hits:
        f, i, m = sqlite_hits[0]
        a.evidence.append(Evidence(f.path, i, f.text.splitlines()[i - 1]))
        a.use_database, a.database = False, "SQLite (파일)"
        a.sizing_hints["stateful_local_files"] = True
        a.warn("SQLite 파일에 데이터를 저장합니다", "컨테이너를 다시 띄우면 데이터가 사라지고, 작업을 2개 이상으로 늘리면 서로 다른 데이터를 봅니다. MySQL로 옮기는 걸 권합니다.")
        return
    a.use_database = False


def _detect_environment(a: Analysis, repo: Repo) -> None:
    """.env.example 의 비밀이 아닌 값만 environment로. 비밀·배포가 넣는 변수는 제외 (app-config.schema.json 규칙)"""
    for f in repo.named(".env.example", ".env.sample", ".env.template"):
        for i, line in enumerate(f.text.splitlines(), 1):
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if not m:
                continue
            name, value = m.group(1), m.group(2).strip().strip("\"'")
            if not ENV_NAME.match(name) or ENV_FORBIDDEN.search(name) or name in ENV_MANAGED:
                continue
            if not value or _PLACEHOLDER.match(value) or "<가림>" in value or "://" in value:
                continue
            if len(a.environment) >= ENV_MAX:
                a.warn("환경 변수가 너무 많습니다", f"앞의 {ENV_MAX}개만 넣었습니다.")
                return
            a.environment[name] = value
            a.evidence.append(Evidence(f.path, i, line))
        return


def _detect_hints(a: Analysis, repo: Repo, family: Optional[str], deps: dict) -> None:
    heavy = sorted(d for d in deps if d in MEMORY_HEAVY)
    workers = sorted(d for d in deps if d in WORKERS)
    a.sizing_hints.update(
        {
            "runtime": family or "unknown",
            "memory_heavy": bool(heavy),
            "slow_startup": family == "jvm" or bool(heavy),
            "stateful_local_files": bool(a.sizing_hints.get("stateful_local_files")),
            "background_workers": bool(workers),
        }
    )
    if heavy:
        a.info("메모리를 많이 쓰는 라이브러리가 있습니다", ", ".join(heavy))
    if workers:
        a.warn("백그라운드 작업이 있습니다", f"{', '.join(workers)}: 웹 서버와 같은 컨테이너에서만 돌고, 작업을 늘리면 중복 실행될 수 있습니다.")


def _compose_services(text: str) -> Dict[str, Dict[str, str]]:
    """docker-compose를 YAML 라이브러리 없이 필요한 만큼만 읽음: 서비스별 build 위치, dockerfile, command, image, ports 유무"""
    services: Dict[str, Dict[str, str]] = {}
    body = text.split("services:", 1)[-1]
    current = None
    for line in body.splitlines():
        if re.match(r"^\S", line):  # 최상위 키(volumes: 등)를 만나면 끝
            break
        m = re.match(r"^  ([\w.-]+):\s*$", line)
        if m:
            current = services.setdefault(m.group(1), {})
            continue
        if current is None:
            continue
        kv = re.match(r"^\s{4,}(build|context|dockerfile|command|image|ports):\s*(.*)$", line)
        if kv:
            current[kv.group(1)] = kv.group(2).strip().strip("\"'")
    return services


def _parse_command(value: str) -> Optional[List[str]]:
    value = value.strip()
    if value.startswith("["):
        try:
            cmd = json.loads(value)
        except ValueError:
            return None
        return [str(c) for c in cmd] if isinstance(cmd, list) and cmd else None
    parts = value.split()
    return parts or None


# compose 보조 서비스 중 배포에서 대신하거나 필요 없는 이미지
_COMPOSE_REPLACED = {"mysql", "mariadb"}  # 공용 RDS MySQL이 대신함
_COMPOSE_DEV_ONLY = {"adminer", "phpmyadmin"}  # 개발용 DB 관리 화면


def _check_shape(a: Analysis, repo: Repo, family: Optional[str]) -> None:
    dockerfiles = sorted((f.path for f in repo.named("Dockerfile")), key=lambda p: (p.count("/"), p))
    a.has_dockerfile = bool(dockerfiles)
    if not dockerfiles:
        a.unresolved["dockerfile"] = "Dockerfile이 없습니다. 배포하려면 Dockerfile이 필요합니다."
        a.info("Dockerfile이 없습니다", "배포하려면 Dockerfile이 필요합니다. 프레임워크에 맞는 Dockerfile을 만들어야 합니다.")
    elif "Dockerfile" in dockerfiles or len(dockerfiles) == 1:
        a.dockerfile = dockerfiles[0]
    else:
        a.unresolved["dockerfile"] = f"Dockerfile이 여러 개라 어느 것을 쓸지 정해야 합니다: {', '.join(dockerfiles)}"

    for f in repo.named("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        built = {name: svc for name, svc in _compose_services(f.text).items() if "build" in svc or "dockerfile" in svc}
        images = {svc.get("dockerfile") or svc.get("context") or svc.get("build") for svc in built.values()}
        if len(images) > 1:
            a.supported = False
            a.unsupported_reasons.append(f"서로 다른 이미지를 빌드하는 서비스가 여러 개입니다({', '.join(built)}). 앱 1개 배포만 지원합니다.")
            continue
        # 빌드 없이 이미지만 쓰는 보조 서비스(Redis 등)는 배포하지 않으므로 앱이 실패함 → 미지원 (PR #25 재리뷰).
        # MySQL·MariaDB는 공용 DB가 대신하고, DB 관리 화면은 배포에 필요 없음
        extra = []
        for name, svc in _compose_services(f.text).items():
            if name in built or not svc.get("image"):
                continue
            base = svc["image"].strip("\"'").split("@")[0].rsplit("/", 1)[-1].split(":")[0].lower()
            if base not in _COMPOSE_REPLACED and base not in _COMPOSE_DEV_ONLY:
                extra.append(f"{name}({svc['image'].strip(chr(34) + chr(39))})")
        if extra:
            a.supported = False
            a.unsupported_reasons.append(
                f"앱 말고도 따로 띄우는 서비스가 있습니다: {', '.join(extra)}. 지금은 앱 1개와 공용 MySQL만 배포합니다.")
            i, line = _line_of(f, "image:")
            a.evidence.append(Evidence(f.path, i, line))
            continue
        # 같은 이미지로 command만 바꿔 한 번 도는 서비스 = 초기화 작업 (포트를 열지 않음)
        for name, svc in built.items():
            if "command" in svc and "ports" not in svc and a.init_command is None:
                cmd = _parse_command(svc["command"])
                if cmd:
                    a.init_command = cmd
                    i, line = _line_of(f, "command:")
                    for j, l in enumerate(f.text.splitlines(), 1):
                        if l.strip().startswith("command:") and svc["command"].split()[0].strip("[\"'") in l:
                            i, line = j, l
                    a.evidence.append(Evidence(f.path, i, line))
                    a.info("배포 뒤 초기화 명령을 한 번 실행합니다", f"{name} 서비스의 명령({' '.join(cmd)})을 앱 이미지로 실행합니다. 여러 번 실행돼도 안전해야 합니다.")

    if a.dockerfile:
        _check_dockerfile_copies(a, repo, a.dockerfile)
    if family is None and not a.has_dockerfile:
        a.supported = False
        a.unsupported_reasons.append("웹 서버 프레임워크도 Dockerfile도 찾지 못했습니다.")


def _check_dockerfile_copies(a: Analysis, repo: Repo, dockerfile: str) -> None:
    """Dockerfile이 소스에 없는 경로를 복사하면 빌드가 실패함 (예: 저장소 루트 기준으로 쓴 COPY를 하위 폴더만 올렸을 때)"""
    f = repo.by_path[dockerfile]
    paths = {p.path for p in repo.files}
    missing: List[str] = []
    for i, line in enumerate(f.text.splitlines(), 1):
        m = re.match(r"(?i)^\s*(?:COPY|ADD)\s+(?!--from)(?:--\S+\s+)*(.+)$", line)
        if not m:
            continue
        args = m.group(1).split()
        for src in args[:-1]:
            src = src.strip("\"'[],")
            src = src[2:] if src.startswith("./") else src
            if not src or src == "." or "*" in src or src.startswith("http"):
                continue
            if src not in paths and not any(p.startswith(src.rstrip("/") + "/") for p in paths):
                missing.append(src)
                a.evidence.append(Evidence(f.path, i, line))
                break
    if missing:
        a.warn(
            "Dockerfile이 소스에 없는 경로를 복사합니다",
            f"{dockerfile}이 복사하는 {', '.join(missing)}가 올린 소스 안에 없습니다. "
            "빌드가 실패할 수 있으니 Dockerfile이 기준으로 삼는 폴더 전체를 올렸는지 확인해 주세요.",
        )


def _suggest_dockerfile(a: Analysis, files: List[SourceFile], family: Optional[str], py: dict) -> None:
    """Dockerfile이 없으면 규칙 템플릿으로 초안을 만들어 보여 줌. 배포는 여전히 Dockerfile이 저장소에 있어야 함"""
    draft, why = draft_dockerfile(files, family, a.framework, a.runtime, a.container_port, py)
    a.findings = [f for f in a.findings if f.title != "Dockerfile이 없습니다"]
    if draft is None:
        a.info("Dockerfile이 없습니다", f"배포하려면 Dockerfile이 필요합니다. 초안도 만들지 못했습니다: {why}")
        return
    a.dockerfile_draft = draft.as_dict()
    a.unresolved["dockerfile"] = "Dockerfile이 없습니다. 아래 초안을 저장소 루트에 Dockerfile 로 추가하고 다시 올려 주세요."
    a.info("Dockerfile 초안을 만들었습니다",
           f"코드를 보고 {a.framework}용 초안을 만들었습니다(포트 {draft.port}). 내용을 확인하고 저장소에 추가한 뒤 다시 올리면 배포할 수 있습니다.")


def analyze_files(files: List[SourceFile]) -> Analysis:
    """원본 파일 목록 → 가린 뒤 규칙으로 분석. 원본은 바꾸지 않음."""
    report: MaskReport = mask_files(files)
    repo = Repo(report.files)
    a = Analysis(withheld_files=report.withheld, redactions=report.redactions)

    py, node, jvm = _python_deps(repo), _node_deps(repo), _jvm_deps(repo)
    deps = {**py, **node}

    family = _detect_framework(a, py, node, jvm)
    if family is None:
        a.unresolved["framework"] = "알려진 웹 프레임워크를 찾지 못했습니다."
    _detect_runtime(a, repo, family)
    _detect_port(a, repo)
    _detect_health(a, repo)
    _detect_database(a, repo, deps, jvm)
    _detect_environment(a, repo)
    _detect_hints(a, repo, family, deps)
    _check_shape(a, repo, family)
    if not a.has_dockerfile and a.supported:
        _suggest_dockerfile(a, report.files, family, py)
    if report.withheld:
        a.info("비밀 파일은 분석에서 뺐습니다", f"{', '.join(report.withheld)}: 변수 이름만 보고 값은 보내지 않았습니다.")

    # 같은 근거가 여러 번 들어가지 않게
    seen, unique = set(), []
    for e in a.evidence:
        if (e.file, e.line) not in seen:
            seen.add((e.file, e.line))
            unique.append(e)
    a.evidence = unique
    return a
