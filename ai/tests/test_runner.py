"""runner 검사. back/app/main.py 의 분석 기록 동작을 흉내 낸 가짜 백엔드로 돈다 (DB·네트워크 밖 접속 없음)."""

import hashlib
import io
import json
import logging
import shutil
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import runner
from paved_ai.masking import backend_unsafe_paths

APP = {
    "requirements.txt": "fastapi==0.142.4\nuvicorn\nPyMySQL==1.1.2\n",
    "app/main.py": 'import os\nfrom fastapi import FastAPI\napp = FastAPI()\nurl = os.getenv("DATABASE_URL")\n@app.get("/health")\ndef health():\n    return {"ok": True}\n',
    "Dockerfile": 'FROM python:3.12-slim\nCOPY . /app\nEXPOSE 8000\nCMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n',
    ".env": "DATABASE_URL=mysql://admin:FAKE-real-password@db/app\n",
}


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


class FakeBackend:
    """GET /api/projects(페이지), GET .../analyses/latest, POST .../analyses 만 흉내"""

    def __init__(self):
        self.projects = []  # {"id", "name", "source_sha256"}
        self.analyses = {}  # id → 기록된 body
        self.posts = 0
        self.page_size = 100
        self.force_reject = False  # 비밀값 검사 422를 강제로 냄
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/api/projects":
                    q = parse_qs(u.query)
                    start = int(q.get("cursor", ["0"])[0])
                    items = backend.projects[start : start + backend.page_size]
                    nxt = start + backend.page_size
                    return self._send(200, {"items": items, "next_cursor": str(nxt) if nxt < len(backend.projects) else None})
                parts = u.path.strip("/").split("/")
                if len(parts) == 5 and parts[3] == "analyses" and parts[4] == "latest":
                    found = backend.analyses.get(parts[2])
                    return self._send(200, found) if found else self._send(404, {"error": "분석 결과가 아직 없습니다."})
                self._send(404, {"error": "없음"})

            def do_POST(self):
                parts = urlparse(self.path).path.strip("/").split("/")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                backend.posts += 1
                project = next((p for p in backend.projects if p["id"] == parts[2]), None)
                if project is None:
                    return self._send(404, {"error": "프로젝트를 찾을 수 없습니다."})
                if body["source_sha256"] != project["source_sha256"]:
                    return self._send(409, {"error": "분석한 ZIP이 현재 프로젝트 파일과 일치하지 않습니다."})
                bad = backend.force_reject or backend_unsafe_paths(body["result"])
                if bad:
                    return self._send(422, {"error": "비밀값으로 보이는 내용은 저장할 수 없습니다: result"})
                backend.analyses[parts[2]] = {"id": "a-" + parts[2], "project_id": parts[2], **body}
                self._send(201, backend.analyses[parts[2]])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def setUpModule():
    # 실패 시나리오 로그가 테스트 출력에 섞이지 않게
    logging.getLogger("ai-runner").setLevel(logging.CRITICAL)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.uploads = Path(tempfile.mkdtemp())
        self.api = runner.Api(self.backend.url, timeout=5)
        self.state = runner.State()
        self.now = 1000.0

    def tearDown(self):
        self.backend.close()
        shutil.rmtree(self.uploads, ignore_errors=True)

    def add_project(self, pid: str, files: dict = APP, sha: str = None, **extra) -> bytes:
        data = make_zip(files)
        (self.uploads / f"{pid}.zip").write_bytes(data)
        self.backend.projects.append({"id": pid, "name": pid, "source_sha256": sha or hashlib.sha256(data).hexdigest(), **extra})
        return data

    def run_once(self):
        return runner.run_once(self.api, self.uploads, self.state, clock=lambda: self.now)

    def test_records_analysis_once(self):
        self.add_project("p1")
        self.assertEqual(self.run_once()["recorded"], 1)
        body = self.backend.analyses["p1"]
        self.assertEqual(body["schema_version"], "analysis/1")
        self.assertEqual(body["result"]["app_config"]["container_port"], 8000)
        self.assertEqual(body["result"]["app_config"]["health_check_path"], "/health")
        self.assertIsNone(body["result"]["scale"])
        # 다시 돌려도 중복 기록하지 않음
        self.assertEqual(self.run_once()["recorded"], 0)
        self.assertEqual(self.backend.posts, 1)

    def test_secret_in_upload_never_sent(self):
        self.add_project("p1")
        self.run_once()
        sent = json.dumps(self.backend.analyses["p1"], ensure_ascii=False)
        self.assertNotIn("FAKE-real-password", sent)
        self.assertIn(".env", self.backend.analyses["p1"]["result"]["masking"]["withheld_files"])

    def test_skips_projects_already_analyzed(self):
        self.add_project("p1")
        self.backend.analyses["p1"] = {"id": "old", "result": {}}
        self.assertEqual(self.run_once()["recorded"], 0)
        self.assertEqual(self.backend.posts, 0)

    def test_fingerprint_mismatch_is_not_analyzed(self):
        self.add_project("p1", sha="0" * 64)
        counts = self.run_once()
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(self.backend.posts, 0, "지문이 다르면 보내지 않음")
        self.assertIn("p1", self.state.stopped)

    def test_missing_zip_retries_then_stops(self):
        self.backend.projects.append({"id": "gone", "name": "gone", "source_sha256": "a" * 64})
        for i in range(runner.MAX_FAILURES):
            self.run_once()
            self.assertEqual(self.state.failures["gone"], i + 1)
            self.now += runner.RETRY_AFTER + 1
        self.assertIn("gone", self.state.stopped)
        # 기다리는 동안에는 다시 시도하지 않음
        self.state = runner.State()
        self.run_once()
        self.now += 10
        self.run_once()
        self.assertEqual(self.state.failures["gone"], 1)

    def test_secret_rejection_is_not_retried(self):
        self.backend.force_reject = True
        self.add_project("p1")
        counts = self.run_once()
        self.assertEqual(counts["failed"], 1)
        self.assertIn("p1", self.state.stopped)
        self.now += runner.RETRY_AFTER * 10
        self.run_once()
        self.assertEqual(self.backend.posts, 1, "같은 결과를 계속 보내지 않음")

    def test_scale_is_passed_through_unchanged(self):
        user_input = {"expected_users": "~1,000", "traffic_pattern": "peak", "monthly_budget_usd": 30, "purpose": "동아리"}
        self.add_project("nested", scale=user_input)
        self.add_project("flat", expected_users="~100", traffic_pattern="steady", monthly_budget_usd="12.5")
        self.add_project("none")
        self.run_once()
        scale = lambda pid: self.backend.analyses[pid]["result"]["scale"]
        self.assertEqual(scale("nested"), {"expected_users": "~1,000", "traffic_pattern": "peak", "monthly_budget_usd": 30.0})
        self.assertEqual(scale("flat"), {"expected_users": "~100", "traffic_pattern": "steady", "monthly_budget_usd": 12.5})
        self.assertIsNone(scale("none"))

    def test_invalid_scale_values_are_dropped_not_guessed(self):
        self.assertIsNone(runner.scale_from_project({"scale": {"expected_users": "많음", "monthly_budget_usd": -5}}))
        self.assertEqual(
            runner.scale_from_project({"scale": {"expected_users": "~10,000", "traffic_pattern": "busy", "monthly_budget_usd": True}}),
            {"expected_users": "~10,000"},
        )

    def test_pagination(self):
        self.backend.page_size = 2
        for i in range(5):
            self.add_project(f"p{i}")
        self.assertEqual(self.run_once()["recorded"], 5)

    def test_backend_down_does_not_crash(self):
        down = runner.Api("http://127.0.0.1:9", timeout=1)
        counts = runner.run_once(down, self.uploads, self.state)
        self.assertEqual(counts, {"recorded": 0, "skipped": 0, "failed": 0})

    def test_once_exit_code(self):
        self.add_project("p1")
        code = runner.main(["--once", "--no-llm", "--api-url", self.backend.url, "--upload-dir", str(self.uploads)])
        self.assertEqual(code, 0)
        self.assertIn("p1", self.backend.analyses)


if __name__ == "__main__":
    unittest.main()
