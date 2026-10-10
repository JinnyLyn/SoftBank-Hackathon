from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ConfigurationError(RuntimeError):
    """Raised when a required backend setting is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    name: str


def database_settings() -> DatabaseSettings:
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise ConfigurationError("DATABASE_URL is required for database operations")

    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ConfigurationError("DATABASE_URL must use the mysql:// scheme")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise ConfigurationError("DATABASE_URL must include user, host, and database name")
    try:
        port = parsed.port or 3306
    except ValueError as exc:
        raise ConfigurationError("DATABASE_URL contains an invalid port") from exc

    return DatabaseSettings(
        host=parsed.hostname,
        port=port,
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        name=unquote(parsed.path.lstrip("/")),
    )


def upload_directory() -> Path:
    default = Path(__file__).resolve().parent.parent / "data" / "uploads"
    configured = os.getenv("UPLOAD_DIR", str(default))
    path = Path(configured).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def plan_artifact_directory() -> Path:
    default = Path(__file__).resolve().parent.parent / "data" / "plans"
    configured = os.getenv("PLAN_ARTIFACT_DIR", str(default))
    path = Path(configured).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        # Windows ACLs, rather than POSIX mode bits, govern local file access.
        pass
    return path


def max_upload_bytes() -> int:
    value = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
    if value < 1:
        raise ConfigurationError("MAX_UPLOAD_BYTES must be positive")
    return value


