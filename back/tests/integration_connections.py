"""실제 로컬 MySQL을 사용하는 AWS worker 연결 API 통합 검사.

실행 전 DATABASE_URL을 loopback MySQL로 설정하고 008 migration을 적용한다.
테스트는 생성한 연결 행을 종료 시 삭제하며 AWS에 연결하지 않는다.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


BACK_DIR = Path(__file__).resolve().parents[1]
WORKER_TOKEN = "local-integration-worker-token"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"mysql", "mysql+pymysql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("DATABASE_URL을 로컬 MySQL(loopback 주소)로 설정해야 합니다.")
    if not parsed.username or not parsed.path.strip("/"):
        raise SystemExit("DATABASE_URL에 사용자명과 데이터베이스 이름이 필요합니다.")
    return value


def _call(base_url: str, path: str, *, method: str = "GET", payload: dict | None = None,
          headers: dict[str, str] | None = None, expected: int = 200) -> dict | None:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(base_url + path, data=data, method=method, headers=request_headers)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise AssertionError(f"{method} {path}: HTTP {response.status}, expected {expected}")
        if response.status == 204:
            return None
        return json.loads(response.read())


def _child_environment(database_url: str, temporary: str) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    env.update({
        "DATABASE_URL": database_url,
        "WORKER_API_TOKEN": WORKER_TOKEN,
        "CORS_ORIGINS": "",
        "UPLOAD_DIR": str(Path(temporary) / "uploads"),
        "PLAN_ARTIFACT_DIR": str(Path(temporary) / "plans"),
        "AWS_EC2_METADATA_DISABLED": "true",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def run() -> None:
    database_url = _database_url()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    created_ids: list[str] = []

    with tempfile.TemporaryDirectory(prefix="paved-connection-integration-") as temporary:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--no-access-log"],
            cwd=BACK_DIR,
            env=_child_environment(database_url, temporary),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError("백엔드 API가 준비되기 전에 종료됐습니다.")
                try:
                    ready = _call(base_url, "/ready")
                    if ready == {"status": "ready", "database": "ok"}:
                        break
                except (URLError, TimeoutError, ConnectionError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("백엔드 API 시작 제한 시간(30초)을 초과했습니다.")
                    time.sleep(0.15)

            worker_headers = {"X-Worker-Token": WORKER_TOKEN}
            _call(base_url, "/api/worker/connections/pending", expected=401)

            connected = _call(
                base_url,
                "/api/connections",
                method="POST",
                expected=201,
                payload={"provider": "aws", "name": "local-mysql-connection-test", "fields": {}},
            )
            assert connected is not None
            created_ids.append(connected["id"])
            assert connected["status"] == "pending"

            pending = _call(base_url, "/api/worker/connections/pending", headers=worker_headers)
            row = next(item for item in pending if item["id"] == connected["id"])
            assert row["external_id"] and row["provider"] == "aws"

            account_id = "123456789012"
            role_arn = f"arn:aws:iam::{account_id}:role/PavedCloudsReadOnlyRole"
            callback_path = f"/api/connections/{connected['id']}/role-callback"
            _call(
                base_url, callback_path, method="POST", expected=404,
                payload={"external_id": "pc-" + "0" * 32, "account_id": account_id, "role_arn": role_arn},
            )
            callback = {
                "external_id": row["external_id"],
                "account_id": account_id,
                "role_arn": role_arn,
            }
            callback_result = _call(
                base_url, callback_path, method="POST", payload=callback,
            )
            assert callback_result is not None and callback_result["roleArn"] == role_arn
            _call(base_url, callback_path, method="POST", payload=callback)  # callback retry is idempotent
            callback_conflict = {**callback, "role_arn": f"arn:aws:iam::{account_id}:role/Other"}
            _call(base_url, callback_path, method="POST", expected=409, payload=callback_conflict)
            pending = _call(base_url, "/api/worker/connections/pending", headers=worker_headers)
            row = next(item for item in pending if item["id"] == connected["id"])
            assert row["account_id"] == account_id and row["role_arn"] == role_arn

            _call(
                base_url, f"/api/worker/connections/{connected['id']}/complete", method="POST",
                headers=worker_headers, expected=409,
                payload={"account_id": account_id, "role_arn": f"arn:aws:iam::{account_id}:role/Other"},
            )
            _call(
                base_url, f"/api/worker/connections/{connected['id']}/complete", method="POST",
                headers=worker_headers, expected=422,
                payload={"account_id": account_id, "role_arn": "arn:aws:iam::210987654321:role/Other"},
            )

            def complete_once(_: int) -> dict:
                result = _call(
                    base_url, f"/api/worker/connections/{connected['id']}/complete", method="POST",
                    headers=worker_headers, payload={"account_id": account_id, "role_arn": role_arn},
                )
                assert result is not None
                return result

            with ThreadPoolExecutor(max_workers=2) as pool:
                completed = list(pool.map(complete_once, range(2)))
            assert all(item["status"] == "connected" for item in completed)
            assert all(item["accountId"] == account_id and item["roleArn"] == role_arn for item in completed)
            _call(
                base_url, f"/api/worker/connections/{connected['id']}/complete", method="POST",
                headers=worker_headers, payload={"account_id": account_id, "role_arn": role_arn},
            )
            _call(
                base_url, f"/api/worker/connections/{connected['id']}/complete", method="POST",
                headers=worker_headers, expected=409,
                payload={"account_id": account_id, "role_arn": f"arn:aws:iam::{account_id}:role/Other"},
            )

            legacy = _call(
                base_url, "/api/connections", method="POST", expected=201,
                payload={"provider": "aws", "name": "legacy-connected-row-test", "fields": {}},
            )
            assert legacy is not None
            created_ids.append(legacy["id"])
            _call(
                base_url, f"/api/worker/connections/{legacy['id']}/complete", method="POST",
                headers=worker_headers, payload={"account_id": account_id, "role_arn": role_arn},
            )
            # Reproduce rows that were connected before migration 008 added ARN columns.
            sys.path.insert(0, str(BACK_DIR))
            from app.database import connect
            with connect() as db, db.cursor() as cursor:
                cursor.execute(
                    "UPDATE connections SET aws_account_id = NULL, role_arn = NULL WHERE id = %s",
                    (legacy["id"],),
                )
                db.commit()
            repaired = _call(
                base_url, f"/api/worker/connections/{legacy['id']}/complete", method="POST",
                headers=worker_headers, payload={"account_id": account_id, "role_arn": role_arn},
            )
            assert repaired is not None and repaired["status"] == "connected"
            assert repaired["accountId"] == account_id and repaired["roleArn"] == role_arn

            failed_connection = _call(
                base_url, "/api/connections", method="POST", expected=201,
                payload={"provider": "aws", "name": "local-mysql-connection-failure-test", "fields": {}},
            )
            assert failed_connection is not None
            created_ids.append(failed_connection["id"])
            fake_secret = "SYNTHETIC_CONNECTION_TEST_SECRET"
            failure = {"error": f"AWS_SECRET_ACCESS_KEY={fake_secret}"}
            failed = _call(
                base_url, f"/api/worker/connections/{failed_connection['id']}/fail", method="POST",
                headers=worker_headers, payload=failure,
            )
            assert failed is not None and failed["status"] == "error"
            assert fake_secret not in json.dumps(failed)
            assert "REDACTED" in (failed["error"] or "")
            _call(
                base_url, f"/api/worker/connections/{failed_connection['id']}/fail", method="POST",
                headers=worker_headers, payload=failure,
            )
            _call(
                base_url, f"/api/worker/connections/{failed_connection['id']}/fail", method="POST",
                headers=worker_headers, expected=409, payload={"error": "different failure"},
            )

            print("PASS: 로컬 MySQL 연결·콜백·pending 역할 전달·기존 connected 행 보완·worker 검증·동시 완료·실패 마스킹")
        finally:
            for connection_id in created_ids:
                try:
                    _call(base_url, f"/api/connections/{connection_id}", method="DELETE", expected=204)
                except Exception:
                    pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    run()
