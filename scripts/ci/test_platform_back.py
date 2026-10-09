#!/usr/bin/env python3
"""플랫폼 백엔드의 DB 없는 실제 HTTP smoke 검사. 외부 서비스는 검사하지 않는다."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


STARTUP_TIMEOUT = 30
REQUEST_TIMEOUT = 5


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def json(self, path, status=200, method="GET", payload=None, timeout=REQUEST_TIMEOUT):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            require(response.status == status, f"{method} {path}: expected HTTP {status}, got {response.status}")
            require(response.headers.get_content_type() == "application/json", f"{path}: expected JSON")
            body = response.read(1_000_001)
            require(len(body) <= 1_000_000, f"{path}: response too large")
            result = json.loads(body)
        require(isinstance(result, dict), f"{path}: expected JSON object")
        if status >= 400:
            require(isinstance(result.get("error"), str) and bool(result["error"]), f"{path}: missing JSON error")
        return result


def child_environment(directory):
    # Allowlist only OS runtime settings; never inherit service credentials or proxies.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({
        "PATH": os.defpath,
        "TMPDIR": str(directory),
        "TMP": str(directory),
        "TEMP": str(directory),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": "",
        "WORKER_API_TOKEN": "",
        "CORS_ORIGINS": "",
        "UPLOAD_DIR": str(directory / "uploads"),
        "PLAN_ARTIFACT_DIR": str(directory / "plans"),
        "AWS_EC2_METADATA_DISABLED": "true",
    })
    return env


def check(root):
    require((root / "app" / "main.py").is_file(), f"백엔드 진입점이 없습니다: {root / 'app/main.py'}")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    client = Client(port)
    with tempfile.TemporaryDirectory(prefix="platform-back-smoke-") as temporary:
        directory = Path(temporary)
        with (directory / "server.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", str(port), "--no-access-log"],
                cwd=root, env=child_environment(directory), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + STARTUP_TIMEOUT
                while True:
                    require(process.poll() is None, f"백엔드가 조기 종료되었습니다: {process.returncode}")
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, "백엔드 시작 제한 시간(30초)을 초과했습니다")
                    try:
                        health = client.json("/health", timeout=min(REQUEST_TIMEOUT, remaining))
                        break
                    except (URLError, TimeoutError, ConnectionError):
                        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                require(health == {"status": "ok"}, "/health: expected status ok")
                schema = client.json("/openapi.json")
                require(isinstance(schema.get("openapi"), str), "/openapi.json: missing schema version")
                paths = schema.get("paths")
                require(isinstance(paths, dict), "/openapi.json: missing paths")
                for path, method in (("/health", "get"), ("/ready", "get"),
                                     ("/api/worker/deployments/claim", "post"), ("/api/plans", "post")):
                    require(isinstance(paths.get(path), dict) and method in paths[path],
                            f"/openapi.json: missing {method.upper()} {path}")
                client.json("/ready", status=503)
                client.json("/api/worker/deployments/claim", status=401, method="POST")
                client.json("/api/plans", status=422, method="POST", payload={})
                require(process.poll() is None, "검사 중 백엔드가 종료되었습니다")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    print("PASS: 플랫폼 백엔드 시작·OpenAPI·health·DB 미설정 503·작업자 인증 401·입력 검증 422")
    print("검사 제외: 실제 DB·프런트 연동·LLM·AWS 배포")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2] / "back",
                        help="백엔드 경로 (기본: 저장소의 back/)")
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (AssertionError, OSError, URLError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
