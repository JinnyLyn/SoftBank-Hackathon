from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from app.settings import max_upload_bytes, plan_artifact_directory, upload_directory

MAX_ZIP_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1_000
MAX_TERRAFORM_PLAN_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    sha256: str
    size_bytes: int


async def save_project_zip(chunks: AsyncIterator[bytes], filename: str, project_id: UUID) -> StoredUpload:
    if not filename or Path(filename).suffix.lower() != ".zip":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ZIP 파일만 업로드할 수 있습니다.")

    directory = upload_directory()
    final_path = directory / f"{project_id}.zip"
    digest = hashlib.sha256()
    size = 0
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f"{project_id}-", suffix=".part", dir=directory)
    try:
        with os.fdopen(file_descriptor, "wb") as destination:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_upload_bytes():
                    limit_mib = max_upload_bytes() // (1024 * 1024)
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"ZIP 파일은 {limit_mib} MiB 이하여야 합니다.",
                    )
                digest.update(chunk)
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "빈 ZIP 파일은 업로드할 수 없습니다.")
        if not zipfile.is_zipfile(temporary_name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "유효한 ZIP 파일이 아닙니다.")
        _validate_archive(Path(temporary_name))
        os.replace(temporary_name, final_path)
        return StoredUpload(final_path, digest.hexdigest(), size)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


class _SafeGitHubRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, request: Request, response: object, code: int, message: str,
        headers: object, new_url: str,
    ) -> Request | None:
        parsed = urlsplit(new_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {"api.github.com", "github.com", "codeload.github.com"}:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 다운로드 주소가 허용된 도메인이 아닙니다.")
        # Never forward a GitHub token to codeload or another redirect destination.
        return Request(new_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "paved-clouds-backend"})


def save_github_zip(repository_url: str, ref: str | None, project_id: UUID) -> tuple[StoredUpload, str, str]:
    """Fetch and safely store a GitHub repository archive. Returns upload, canonical URL, resolved ref."""
    try:
        parsed = urlsplit(repository_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "GitHub 저장소 URL 형식이 올바르지 않습니다.") from exc
    pieces = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or port or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(pieces) != 2):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "GitHub 저장소 URL 형식이 올바르지 않습니다.")
    owner, repo = pieces
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo or any(c in owner + repo for c in "\\\x00"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "GitHub 저장소 URL 형식이 올바르지 않습니다.")
    canonical_url = f"https://github.com/{owner}/{repo}"
    token = os.getenv("GITHUB_TOKEN", "").strip()
    api_headers = {"Accept": "application/vnd.github+json", "User-Agent": "paved-clouds-backend"}
    if token:
        api_headers["Authorization"] = f"Bearer {token}"
    opener = build_opener(_SafeGitHubRedirect())
    api_url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    try:
        with opener.open(Request(api_url, headers=api_headers), timeout=20) as response:
            metadata = json.loads(response.read(1024 * 1024))
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub 저장소를 찾을 수 없거나 접근 권한이 없습니다.") from exc
        if exc.code == 403:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "GitHub 요청 한도 또는 저장소 권한을 확인해 주세요.") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 저장소 정보를 가져오지 못했습니다.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 저장소에 연결할 수 없습니다.") from exc
    resolved_ref = (ref or metadata.get("default_branch"))
    if not isinstance(resolved_ref, str) or not resolved_ref.strip() or len(resolved_ref) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "GitHub 기본 브랜치 또는 ref가 올바르지 않습니다.")
    resolved_ref = resolved_ref.strip()
    archive_url = (f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
                   f"/zipball/{quote(resolved_ref, safe='/')}")
    directory = upload_directory()
    final_path = directory / f"{project_id}.zip"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{project_id}-", suffix=".part", dir=directory)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            with opener.open(Request(archive_url, headers=api_headers), timeout=30) as response:
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > max_upload_bytes():
                        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "GitHub ZIP은 업로드 크기 제한을 초과했습니다.")
                    digest.update(chunk)
                    destination.write(chunk)
        if size == 0 or not zipfile.is_zipfile(temporary_name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "GitHub가 유효한 ZIP 아카이브를 반환하지 않았습니다.")
        _validate_archive(Path(temporary_name))
        os.replace(temporary_name, final_path)
        return StoredUpload(final_path, digest.hexdigest(), size), canonical_url, resolved_ref
    except HTTPError as exc:
        Path(temporary_name).unlink(missing_ok=True)
        if exc.code == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub ref를 찾을 수 없거나 접근 권한이 없습니다.") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 저장소 아카이브를 다운로드하지 못했습니다.") from exc
    except (URLError, TimeoutError) as exc:
        Path(temporary_name).unlink(missing_ok=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 아카이브 다운로드에 실패했습니다.") from exc
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


async def save_terraform_plan(
    chunks: AsyncIterator[bytes], plan_id: UUID, expected_sha256: str
) -> StoredUpload:
    directory = plan_artifact_directory()
    # A request owns its own immutable artifact path. Concurrent failed requests
    # must never unlink another request's successfully committed plan.
    final_path = directory / f"{plan_id}-{uuid4().hex}.tfplan"
    digest = hashlib.sha256()
    size = 0
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f"{plan_id}-", suffix=".part", dir=directory)
    try:
        os.chmod(temporary_name, 0o600)
        with os.fdopen(file_descriptor, "wb") as destination:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_TERRAFORM_PLAN_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "Terraform plan 파일은 50 MiB 이하여야 합니다.",
                    )
                digest.update(chunk)
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "빈 Terraform plan 파일은 저장할 수 없습니다.")
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Terraform plan SHA-256이 요청값과 일치하지 않습니다.")
        os.replace(temporary_name, final_path)
        try:
            final_path.chmod(0o600)
        except OSError:
            pass
        return StoredUpload(final_path, digest.hexdigest(), size)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _validate_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ZIP 내부 파일 수가 제한을 초과했습니다.")
            total_size = 0
            for entry in entries:
                name = entry.filename
                relative = PurePosixPath(name)
                windows_path = PureWindowsPath(name)
                if (
                    "\\" in name
                    or relative.is_absolute()
                    or windows_path.is_absolute()
                    or windows_path.drive
                    or ".." in relative.parts
                    or "\x00" in name
                ):
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ZIP에 안전하지 않은 경로가 포함되어 있습니다.")
                mode = entry.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ZIP 심볼릭 링크는 지원하지 않습니다.")
                if entry.flag_bits & 0x1:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "암호화된 ZIP은 지원하지 않습니다.")
                total_size += entry.file_size
                if total_size > MAX_UNCOMPRESSED_BYTES:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ZIP 압축 해제 예상 크기가 제한을 초과했습니다.")
                if entry.file_size and entry.compress_size == 0:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "비정상 압축 파일입니다.")
                if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ZIP 압축률이 안전 제한을 초과했습니다.")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "유효한 ZIP 파일이 아닙니다.") from exc
