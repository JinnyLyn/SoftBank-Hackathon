"""Private local/S3 artifact storage used for uploaded source and Terraform plans."""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import urlsplit

from fastapi import HTTPException, status


def s3_enabled() -> bool:
    return bool(os.getenv("ARTIFACT_S3_BUCKET", "").strip())


def persist_file(path: Path, key: str, content_type: str) -> tuple[Path | None, str]:
    """Keep a local file in development, or move it to private S3 for server mode."""
    bucket = os.getenv("ARTIFACT_S3_BUCKET", "").strip()
    if not bucket:
        return path, str(path)
    reference = upload_file_to_s3(path, key, content_type)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        delete_artifact(reference)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "업로드 임시 파일을 정리하지 못했습니다.") from exc
    return None, reference


def upload_file_to_s3(path: Path, key: str, content_type: str) -> str:
    """Copy a local artifact to private S3 without removing the source file."""
    bucket = os.getenv("ARTIFACT_S3_BUCKET", "").strip()
    if not bucket:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ARTIFACT_S3_BUCKET 설정이 필요합니다.")
    object_key = _object_key(key)
    encryption = os.getenv("ARTIFACT_S3_SSE", "AES256").strip()
    if encryption not in {"AES256", "aws:kms"}:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ARTIFACT_S3_SSE는 AES256 또는 aws:kms여야 합니다.")
    extra_args: dict[str, str] = {"ContentType": content_type, "ServerSideEncryption": encryption}
    kms_key_id = os.getenv("ARTIFACT_S3_KMS_KEY_ID", "").strip()
    if kms_key_id:
        if encryption != "aws:kms":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "KMS 키를 사용할 때 ARTIFACT_S3_SSE는 aws:kms여야 합니다.")
        extra_args["SSEKMSKeyId"] = kms_key_id
    try:
        _s3_client().upload_file(
            str(path), bucket, object_key,
            ExtraArgs=extra_args,
        )
    except Exception as exc:  # SDK exceptions can include request details; do not expose them.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "비공개 파일 저장소에 파일을 저장하지 못했습니다.") from exc
    return f"s3://{bucket}/{object_key}"


def delete_artifact(reference: str | None) -> None:
    if not reference:
        return
    parsed = _parse_s3_reference(reference)
    if parsed is None:
        Path(reference).unlink(missing_ok=True)
        return
    bucket, key = parsed
    try:
        _s3_client().delete_object(Bucket=bucket, Key=key)
    except Exception:
        # Best-effort cleanup; database references are still rolled back by callers.
        pass


def artifact_exists(reference: str | None) -> bool:
    if not reference:
        return False
    parsed = _parse_s3_reference(reference)
    if parsed is None:
        return Path(reference).is_file()
    bucket, key = parsed
    try:
        _s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def artifact_sha256(reference: str | None) -> str | None:
    if not reference:
        return None
    digest = hashlib.sha256()
    parsed = _parse_s3_reference(reference)
    if parsed is None:
        try:
            with open(reference, "rb") as artifact:
                _update_digest(digest, artifact)
        except OSError:
            return None
        return digest.hexdigest()

    bucket, key = parsed
    try:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            _update_digest(digest, body)
        finally:
            body.close()
    except Exception:
        return None
    return digest.hexdigest()


def iter_artifact(reference: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    parsed = _parse_s3_reference(reference)
    if parsed is None:
        with open(reference, "rb") as artifact:
            while chunk := artifact.read(chunk_size):
                yield chunk
        return

    bucket, key = parsed
    response = _s3_client().get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


def _update_digest(digest: "hashlib._Hash", artifact: BinaryIO) -> None:
    while chunk := artifact.read(1024 * 1024):
        digest.update(chunk)


def _parse_s3_reference(reference: str) -> tuple[str, str] | None:
    parsed = urlsplit(reference)
    if parsed.scheme != "s3":
        return None
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError("Invalid S3 artifact reference")
    return bucket, key


def _object_key(key: str) -> str:
    prefix = os.getenv("ARTIFACT_S3_PREFIX", "paved-clouds").strip("/")
    safe_key = key.lstrip("/")
    if not safe_key or ".." in Path(safe_key).parts:
        raise ValueError("Invalid artifact object key")
    return f"{prefix}/{safe_key}" if prefix else safe_key


@lru_cache(maxsize=1)
def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "S3 저장을 사용하려면 백엔드 boto3 의존성을 설치해야 합니다.",
        ) from exc
    region = os.getenv("AWS_REGION", "").strip() or None
    return boto3.client("s3", region_name=region)
