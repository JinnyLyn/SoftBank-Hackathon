"""Dockerfile이 없는 앱에 쓸 Dockerfile 초안. 규칙 템플릿으로 만든다 (LLM을 쓰지 않음).

- 같은 코드면 같은 초안이 나오고, 어떤 파일·줄을 보고 만들었는지 근거를 댈 수 있다.
- 진입점(앱 객체·시작 명령)을 하나로 확정하지 못하면 초안을 만들지 않고 이유를 돌려준다. 추측하지 않는다.
- 초안은 사용자가 확인하는 제안이다. 저장소에 넣고 다시 올리면 그 파일로 빌드한다 (AGENTS.md 5-3).
- 지원: Python(FastAPI·Flask·Django), Node.js. 그 밖은 이유와 함께 만들지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .source import SourceFile

DEFAULT_PYTHON = "3.12"
DEFAULT_NODE = "20"


@dataclass
class Draft:
    content: str
    port: int
    # 초안을 만들 때 본 근거 ("app.py:3 app = Flask(__name__)")
    based_on: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": "Dockerfile", "content": self.content, "port": self.port, "based_on": self.based_on}


def _root(files: List[SourceFile], name: str) -> Optional[SourceFile]:
    return next((f for f in files if f.path == name), None)


def _find_one(files: List[SourceFile], exts: Tuple[str, ...], pattern: re.Pattern):
    """패턴이 정확히 한 곳에서만 나오면 (파일, 줄 번호, 매치). 없거나 여러 곳이면 None"""
    hits = []
    for f in files:
        if not f.path.endswith(exts):
            continue
        for i, line in enumerate(f.text.splitlines(), 1):
            m = pattern.search(line)
            if m:
                hits.append((f, i, m))
    return hits[0] if len(hits) == 1 else None


def _version(runtime: Optional[str], default: str, major_only: bool = False) -> str:
    m = re.search(r"\d+(?:\.\d+)?", runtime or "")
    if not m:
        return default
    return m.group(0).split(".")[0] if major_only else m.group(0)


def _module(path: str) -> Optional[str]:
    """app/main.py → app.main. 모듈 이름으로 쓸 수 없는 경로면 None"""
    mod = path[:-3].replace("/", ".")
    return mod if re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*", mod) else None


def _header(framework: str, based_on: List[str]) -> List[str]:
    return [
        f"# Paved Clouds가 코드를 보고 만든 Dockerfile 초안입니다 ({framework}).",
        "# 근거: " + "; ".join(based_on),
        "# 저장소 루트에 Dockerfile 로 저장하고 다시 올리면 이 파일로 빌드합니다. 필요하면 고쳐서 쓰세요.",
    ]


# ---------- Python ----------

_FASTAPI = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*FastAPI\(")
_FLASK = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*Flask\(")


def _python(files, framework: str, runtime: Optional[str], port: int, deps: Dict[str, tuple]) -> Tuple[Optional[Draft], Optional[str]]:
    based_on: List[str] = []
    if _root(files, "requirements.txt"):
        install = ["COPY requirements.txt .", "RUN pip install --no-cache-dir -r requirements.txt", "COPY . ."]
        based_on.append("requirements.txt")
    elif _root(files, "pyproject.toml"):
        install = ["COPY . .", "RUN pip install --no-cache-dir ."]
        based_on.append("pyproject.toml")
    else:
        return None, "저장소 루트에 requirements.txt 나 pyproject.toml 이 없어 설치 명령을 정할 수 없습니다."

    if framework == "FastAPI":
        hit = _find_one(files, (".py",), _FASTAPI)
        if not hit or not _module(hit[0].path):
            return None, "FastAPI 앱 객체(app = FastAPI())를 한 곳에서 찾지 못했습니다."
        f, i, m = hit
        target = f"{_module(f.path)}:{m.group(1)}"
        server = "uvicorn"
        cmd = ["uvicorn", target, "--host", "0.0.0.0", "--port", str(port)]
    elif framework == "Flask":
        hit = _find_one(files, (".py",), _FLASK)
        if not hit or not _module(hit[0].path):
            return None, "Flask 앱 객체(app = Flask(__name__))를 한 곳에서 찾지 못했습니다(앱 팩토리면 시작 명령을 직접 정해야 합니다)."
        f, i, m = hit
        target = f"{_module(f.path)}:{m.group(1)}"
        server = "gunicorn"
        cmd = ["gunicorn", "-b", f"0.0.0.0:{port}", target]
    elif framework == "Django":
        wsgi = [f for f in files if f.path.endswith("/wsgi.py") and "application" in f.text]
        if not _root(files, "manage.py") or len(wsgi) != 1 or not _module(wsgi[0].path):
            return None, "Django의 manage.py 와 wsgi.py 를 하나로 확정하지 못했습니다."
        f, i = wsgi[0], 1
        target = f"{_module(f.path)}:application"
        server = "gunicorn"
        cmd = ["gunicorn", "-b", f"0.0.0.0:{port}", target]
    else:
        return None, f"{framework} 앱의 초안은 아직 만들지 않습니다."
    line = f.text.splitlines()[i - 1].strip() if f.text else ""
    based_on.append(f"{f.path}:{i} {line}"[:120])

    lines = _header(framework, based_on) + [
        f"FROM python:{_version(runtime, DEFAULT_PYTHON)}-slim",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
        "WORKDIR /app",
        *install,
    ]
    if server not in deps:
        # 의존성 파일에 실행 서버가 없으면 같이 설치 (코드는 바꾸지 않음)
        lines.append(f"RUN pip install --no-cache-dir {server}")
    lines += [f"EXPOSE {port}", "CMD " + json.dumps(cmd)]
    return Draft("\n".join(lines) + "\n", port, based_on), None


# ---------- Node.js ----------

def _node(files, framework: str, runtime: Optional[str], port: int) -> Tuple[Optional[Draft], Optional[str]]:
    pkg_file = _root(files, "package.json")
    if not pkg_file:
        return None, "저장소 루트에 package.json 이 없습니다."
    try:
        pkg = json.loads(pkg_file.text)
    except ValueError:
        return None, "package.json 을 읽지 못했습니다."
    scripts = pkg.get("scripts") or {}
    based_on = ["package.json"]
    if isinstance(scripts.get("start"), str):
        cmd = ["npm", "start"]
        based_on.append(f"package.json scripts.start: {scripts['start']}"[:120])
    else:
        main = pkg.get("main") if isinstance(pkg.get("main"), str) else None
        entry = next((n for n in ([main] if main else []) + ["server.js", "index.js", "app.js"] if _root(files, n)), None)
        if not entry:
            return None, "package.json 의 start 스크립트나 시작 파일(main, server.js, index.js, app.js)을 찾지 못했습니다."
        cmd = ["node", entry]
        based_on.append(entry)
    has_build = isinstance(scripts.get("build"), str)
    lock = _root(files, "package-lock.json") is not None
    # 빌드가 있으면 개발 의존성도 필요함
    install = "npm ci" if lock else "npm install"
    if not has_build:
        install += " --omit=dev"

    lines = _header(framework, based_on) + [
        f"FROM node:{_version(runtime, DEFAULT_NODE, major_only=True)}-slim",
        "ENV NODE_ENV=production",
        "WORKDIR /app",
        "COPY package*.json ./",
        f"RUN {install}",
        "COPY . .",
    ]
    if has_build:
        lines.append("RUN npm run build")
    lines += [f"ENV PORT={port}", f"EXPOSE {port}", "CMD " + json.dumps(cmd)]
    return Draft("\n".join(lines) + "\n", port, based_on), None


def draft_dockerfile(
    files: List[SourceFile], family: Optional[str], framework: Optional[str], runtime: Optional[str],
    port: Optional[int], python_deps: Dict[str, tuple],
) -> Tuple[Optional[Draft], Optional[str]]:
    """(초안, 못 만든 이유). 포트를 모르면 프레임워크 기본값을 쓰고 초안에 그 포트를 적는다"""
    if family == "python" and framework:
        return _python(files, framework, runtime, port or 8000, python_deps)
    if family == "node" and framework:
        return _node(files, framework, runtime, port or 3000)
    return None, "지원하는 웹 프레임워크(Python FastAPI·Flask·Django, Node.js)를 찾지 못해 초안을 만들지 않았습니다."
