"""분석할 소스 파일 목록 만들기.

분석은 "파일 목록(경로 + 내용)"만 받는다. ZIP이든 폴더든, 백엔드가 어떤 방식으로 원본을 주든 여기서 같은 모양으로 바꾼다.
디스크에 풀지 않고 메모리에서만 읽는다 (ZIP 안 경로가 작업 폴더 밖을 덮어쓰는 문제를 원천 차단, AGENTS.md 7).
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

# 분석에 필요 없고 크기만 큰 폴더
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".next", ".nuxt", "target", ".gradle", ".idea", ".vscode", "coverage",
}
MAX_FILE_BYTES = 200_000  # 이보다 큰 파일은 읽지 않음 (번들 결과물, 데이터 파일 등)
MAX_TOTAL_BYTES = 5_000_000
MAX_FILES = 3000


@dataclass(frozen=True)
class SourceFile:
    path: str  # 저장소 기준 상대 경로, "/" 구분
    text: str


class SourceError(ValueError):
    pass


def _safe_path(name: str) -> str | None:
    """ZIP 안 경로를 정리. 절대 경로나 상위 폴더(..)로 나가는 경로는 버림."""
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    norm = posixpath.normpath(name)
    if norm.startswith("../") or norm == ".." or norm == ".":
        return None
    return norm


def _skipped(path: str) -> bool:
    return any(part in SKIP_DIRS for part in path.split("/")[:-1])


def _decode(data: bytes) -> str | None:
    """텍스트 파일만. 널 바이트가 있거나 UTF-8이 아니면 바이너리로 보고 버림."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _strip_common_root(files: List[SourceFile]) -> List[SourceFile]:
    """GitHub ZIP처럼 모든 파일이 폴더 하나(repo-main/) 아래 있으면 그 폴더를 뺌."""
    roots = {f.path.split("/", 1)[0] for f in files}
    if len(roots) == 1 and all("/" in f.path for f in files):
        cut = len(next(iter(roots))) + 1
        return [SourceFile(f.path[cut:], f.text) for f in files]
    return files


def _collect(entries: Iterable[tuple[str, int, "callable"]]) -> List[SourceFile]:
    files: List[SourceFile] = []
    total = 0
    for name, size, read in entries:
        path = _safe_path(name)
        if path is None or _skipped(path) or size > MAX_FILE_BYTES:
            continue
        if len(files) >= MAX_FILES or total + size > MAX_TOTAL_BYTES:
            break
        text = _decode(read())
        if text is None:
            continue
        files.append(SourceFile(path, text))
        total += size
    if not files:
        raise SourceError("분석할 수 있는 텍스트 파일이 없습니다.")
    return sorted(_strip_common_root(files), key=lambda f: f.path)


def files_from_zip(data: bytes) -> List[SourceFile]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceError("ZIP 파일이 아니거나 손상되었습니다.") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        return _collect((i.filename, i.file_size, (lambda i=i: zf.read(i))) for i in infos)


def files_from_dir(root: Path) -> List[SourceFile]:
    root = Path(root)
    paths = [p for p in root.rglob("*") if p.is_file()]
    return _collect((p.relative_to(root).as_posix(), p.stat().st_size, p.read_bytes) for p in paths)
