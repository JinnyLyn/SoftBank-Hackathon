"""비밀값 가리기.

두 가지 용도가 있다.
1. mask_files: LLM에 보내기 전 소스 복사본에서 값을 가린다. 변수 이름은 남긴다 (분석에는 "DB 주소를 환경 변수로 받는다"는 사실만 필요).
2. backend_safe_text / is_backend_safe: 백엔드에 기록하는 분석 결과가 백엔드의 비밀값 검사(back/app/main.py의
   _reject_secret_fields)에 걸리지 않게 한다. 백엔드는 "KEY=값" 모양만 보고 거절하므로 가린 값(KEY=<가림>)도 거절된다.

원본은 바꾸지 않는다. 가린 결과는 새 SourceFile 목록이다 (AGENTS.md 5-2).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import List, Tuple

from .source import SourceFile

MASK = "<가림>"

# 내용을 통째로 보내지 않는 파일. .env 계열은 변수 이름만 남긴다
_SECRET_FILE_GLOBS = ["*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_rsa*", "id_ed25519*", "credentials.json",
                      "*service-account*.json", ".npmrc", ".pypirc", ".netrc"]
_ENV_FILE = re.compile(r"(^|/)\.env(\.[\w-]+)?$")
_ENV_EXAMPLE = re.compile(r"(^|/)\.env\.(example|sample|template|dist)$")

# 비밀로 보는 변수 이름. infra app-config.schema.json·백엔드 규칙과 같은 단어
_SECRET_NAME = r"[A-Za-z0-9_-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|CREDENTIAL|API_?KEY|ACCESS_?KEY)[A-Za-z0-9_-]*"

# 모양으로 알아보는 비밀값
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), MASK),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + MASK),
    # 주소 안의 아이디:비밀번호@ → 통째로 가림 (scheme://<가림>@host)
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@'\"]+:[^/\s@'\"]+@"), r"\1" + MASK + "@"),
]

# 설정 파일의 KEY=값, KEY: 값, - KEY=값 (env·yaml·ini·compose). 따옴표 문자열이거나 공백 없는 값
_ENV_ASSIGN = re.compile(r"(?im)^(\s*(?:-\s*)?(?:export\s+)?(" + _SECRET_NAME + r")\s*[=:]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s#\n]+)")
# 위 규칙을 적용할 설정 파일. 코드에는 적용하지 않음 (SECRET_KEY = os.getenv("SECRET_KEY") 같은 줄의 변수 이름을 지키려고)
_CONFIG_FILE = re.compile(r"(?i)(^|/)(\.env(\.[\w-]+)?|[^/]+\.(ya?ml|ini|cfg|conf|toml|properties)|dockerfile)$")
# 코드에서 비밀 이름 변수에 문자열 그대로 넣은 것만 (password = request.form[...] 같은 코드는 그대로 둠)
_CODE_ASSIGN = re.compile(r"(?i)\b(" + _SECRET_NAME + r")(\s*[:=]\s*)([\"'])([^\"'\n]{4,})\3")


@dataclass(frozen=True)
class MaskReport:
    files: List[SourceFile]
    # 내용을 통째로 뺀 파일
    withheld: List[str]
    # 값을 가린 횟수
    redactions: int


def _is_secret_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if _ENV_FILE.search(path) and not _ENV_EXAMPLE.search(path):
        return True
    return any(fnmatch.fnmatch(name, g) for g in _SECRET_FILE_GLOBS)


def _env_names_only(text: str) -> str:
    """.env 파일은 변수 이름만 남김: KEY=<가림>"""
    lines = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            lines.append(f"{m.group(1)}={MASK}")
    return "\n".join(lines)


def mask_text(text: str, config_file: bool = False) -> Tuple[str, int]:
    count = 0
    for pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        count += n
    if config_file:
        text, n = _ENV_ASSIGN.subn(lambda m: m.group(1) + MASK, text)
        count += n
    text, n = _CODE_ASSIGN.subn(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(3)}", text)
    count += n
    return text, count


def mask_files(files: List[SourceFile]) -> MaskReport:
    out: List[SourceFile] = []
    withheld: List[str] = []
    total = 0
    for f in files:
        if _is_secret_file(f.path):
            withheld.append(f.path)
            if _ENV_FILE.search(f.path):
                out.append(SourceFile(f.path, _env_names_only(f.text)))
            continue
        text, n = mask_text(f.text, config_file=bool(_CONFIG_FILE.search(f.path)))
        total += n
        out.append(SourceFile(f.path, text))
    return MaskReport(out, withheld, total)


# ---------- 백엔드 기록용 ----------
# back/app/main.py 의 _SECRET_KEY·_SECRET_VALUE·_CREDENTIAL_URL·_BEARER·_AWS_ACCESS_KEY·_is_secret_key 와 같은 규칙.
# 백엔드 규칙이 바뀌면 같이 고친다 (tests/test_masking.py 가 백엔드 소스의 정규식과 같은지 확인)
#
# 백엔드는 _SECRET_VALUE 로 모든 "이름=값"을 찾은 뒤 이름이 비밀스러울 때만 거절한다 (_contains_inline_secret, 7d1a668).
# 그래서 "FROM python:3.12" 같은 문장은 통과하고 "SECRET_KEY=<가림>" 은 거절된다.

BACKEND_SECRET_KEY = re.compile(r"(?i)(^|[_-])(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)([_-]|$)")
BACKEND_SECRET_VALUE = re.compile(
    r"""(?ix)(?P<key_quote>["']?)(?P<key>[A-Z_][A-Z0-9_-]*)(?P=key_quote)(?P<separator>\s*[:=]\s*)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"""
)
BACKEND_CREDENTIAL_URL = re.compile(r"(?i)\b(mysql(?:\+pymysql)?|https?)://[^/\s:@]+:[^/\s@]+@")
BACKEND_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
BACKEND_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")


def backend_is_secret_key(key: str) -> bool:
    """백엔드 _is_secret_key: camelCase·PascalCase를 나눈 뒤 비밀 이름 목록과 비교"""
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", separated)
    return BACKEND_SECRET_KEY.search(separated) is not None


def _secret_assignments(text: str):
    return [m for m in BACKEND_SECRET_VALUE.finditer(text) if backend_is_secret_key(m.group("key"))]


def backend_safe_text(text: str) -> str:
    """백엔드가 거절할 모양을 사람이 읽을 수 있는 문장으로 바꿈. "SECRET_KEY=<가림>" → "SECRET_KEY (값 가림)" """
    text = BACKEND_CREDENTIAL_URL.sub(lambda m: f"{m.group(1)}://{MASK}@", text)
    text = BACKEND_BEARER.sub("Bearer (값 가림)", text)
    text = BACKEND_AWS_ACCESS_KEY.sub("(AWS 키 가림)", text)
    return BACKEND_SECRET_VALUE.sub(
        lambda m: f"{m.group('key')} (값 가림)" if backend_is_secret_key(m.group("key")) else m.group(0), text
    )


def backend_unsafe_paths(value: object, path: str = "") -> List[str]:
    """백엔드가 (의도대로라면) 422로 거절할 위치 목록. 비어 있어야 기록할 수 있음."""
    found: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if backend_is_secret_key(str(k)):
                found.append(f"{path}{k} (필드 이름)")
            found += backend_unsafe_paths(v, f"{path}{k}.")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += backend_unsafe_paths(item, f"{path}{i}.")
    elif isinstance(value, str) and (
        _secret_assignments(value)
        or BACKEND_CREDENTIAL_URL.search(value)
        or BACKEND_BEARER.search(value)
        or BACKEND_AWS_ACCESS_KEY.search(value)
    ):
        found.append(path.rstrip(".") or "value")
    return found
