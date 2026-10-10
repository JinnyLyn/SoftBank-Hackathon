"""코드 분석(규칙 기반) 검사. 저장소의 실제 샘플 앱과 작은 가짜 앱으로 확인한다."""

import io
import json
import re
import unittest
import zipfile
from pathlib import Path

from paved_ai.analysis import analyze_files
from paved_ai.masking import backend_unsafe_paths
from paved_ai.source import SourceError, SourceFile, files_from_dir, files_from_zip

REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / "sample-back").is_dir()), None)
SCHEMA_PATH = next(
    (p / "infra/modules/ecs-web-app/app-config.schema.json" for p in Path(__file__).resolve().parents
     if (p / "infra/modules/ecs-web-app/app-config.schema.json").is_file()),
    None,
)


def files(**kv):
    return [SourceFile(path, text) for path, text in kv.items()]


def check_app_config(cfg: dict, schema: dict) -> list:
    """app-config.schema.json의 분석 값 규칙만 직접 확인 (jsonschema 패키지를 추가하지 않으려고)"""
    props, errors = schema["properties"], []
    port = cfg.get("container_port")
    if not isinstance(port, int) or not props["container_port"]["minimum"] <= port <= props["container_port"]["maximum"]:
        errors.append("container_port")
    path = cfg.get("health_check_path")
    if not isinstance(path, str) or not re.match(props["health_check_path"]["pattern"], path):
        errors.append("health_check_path")
    if not isinstance(cfg.get("use_database"), bool):
        errors.append("use_database")
    env = cfg.get("environment")
    env_rule = props["environment"]
    if not isinstance(env, dict) or len(env) > env_rule["maxProperties"]:
        errors.append("environment")
    else:
        names = env_rule["propertyNames"]
        forbidden = names["not"]["anyOf"]
        for k, v in env.items():
            bad = not re.match(names["pattern"], k) or any(
                ("const" in r and k == r["const"]) or ("pattern" in r and re.search(r["pattern"], k)) for r in forbidden
            )
            if bad or not isinstance(v, str):
                errors.append(f"environment.{k}")
    # init_command는 인프라 브랜치(totorosi, 10/10)에서 스키마에 추가됨. main에 아직 없으면 그쪽 규칙(문자열 10개 이하)으로 확인
    if "init_command" in cfg:
        rule = props.get("init_command", {"maxItems": 10})
        cmd = cfg["init_command"]
        if not isinstance(cmd, list) or len(cmd) > rule["maxItems"] or not all(isinstance(c, str) and c for c in cmd):
            errors.append("init_command")
    allowed = set(props) | {"init_command"}
    errors += [f"알 수 없는 키 {k}" for k in cfg if k not in allowed]
    return errors


@unittest.skipIf(REPO_ROOT is None, "sample-back 이 없는 위치에서 실행됨")
class SampleAppTests(unittest.TestCase):
    """sample-back: FastAPI + MySQL, 8000번, /health, compose의 init-db (정답을 알고 있는 앱)"""

    @classmethod
    def setUpClass(cls):
        # 데모처럼 sample-back 과 sample-front 를 함께 올린 경우
        both = files_from_dir(REPO_ROOT / "sample-back")
        cls.demo = [SourceFile("sample-back/" + f.path, f.text) for f in both]
        cls.demo += [SourceFile("sample-front/" + f.path, f.text) for f in files_from_dir(REPO_ROOT / "sample-front")]
        cls.result = analyze_files(cls.demo).to_result()

    def test_app_config_values(self):
        cfg = self.result["app_config"]
        self.assertEqual(cfg["container_port"], 8000)
        self.assertEqual(cfg["health_check_path"], "/health")
        self.assertIs(cfg["use_database"], True)
        self.assertEqual(cfg["environment"], {"COOKIE_SECURE": "true"})
        self.assertEqual(cfg["init_command"], ["python", "-m", "backend.app.initialize_database"])
        self.assertEqual(self.result["dockerfile"], "sample-back/Dockerfile")
        self.assertTrue(self.result["supported"])
        self.assertEqual(self.result["unresolved"], {})

    @unittest.skipIf(SCHEMA_PATH is None, "app-config.schema.json 없음")
    def test_app_config_passes_infra_schema(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(check_app_config(self.result["app_config"], schema), [])

    def test_display_fields_for_frontend(self):
        labels = [s["label"] for s in self.result["stack"]]
        self.assertEqual(labels, ["프레임워크", "런타임", "포트", "DB", "헬스체크", "환경 변수"])
        self.assertEqual(self.result["stack"][0]["value"], "FastAPI")
        self.assertEqual(self.result["stack"][1]["value"], "Python 3.12")
        for f in self.result["findings"]:
            self.assertIn(f["level"], ("info", "warn"))

    def test_evidence_points_to_real_lines(self):
        by_path = {f.path: f.text.splitlines() for f in self.demo}
        self.assertTrue(self.result["evidence"])
        for e in self.result["evidence"]:
            with self.subTest(e=e):
                line = by_path[e["file"]][e["line"] - 1]
                # 근거 문장은 가린 뒤 줄의 일부이거나(비밀값 없음) 같은 줄
                self.assertTrue(e["text"][:20].split("(값 가림)")[0].strip() in line or e["text"] in line)

    def test_result_passes_backend_secret_check(self):
        self.assertEqual(backend_unsafe_paths(self.result), [])

    def test_compose_password_never_reaches_result(self):
        dumped = json.dumps(self.result, ensure_ascii=False)
        for secret in ("launchpad_local_password", "change_this_local_root_password", "replace-me"):
            self.assertNotIn(secret, dumped)

    def test_warns_when_dockerfile_copies_missing_paths(self):
        # sample-back 폴더만 올리면 Dockerfile의 COPY sample-back·sample-front 가 소스에 없음
        only_back = files_from_dir(REPO_ROOT / "sample-back")
        r = analyze_files(only_back).to_result()
        titles = [f["title"] for f in r["findings"]]
        self.assertIn("Dockerfile이 소스에 없는 경로를 복사합니다", titles)
        self.assertEqual(titles.count("Dockerfile이 소스에 없는 경로를 복사합니다"), 1)


class SmallAppTests(unittest.TestCase):
    def test_express_app(self):
        a = analyze_files(files(**{
            "package.json": json.dumps({"dependencies": {"express": "^4.19.0", "mysql2": "^3.0.0"}}),
            "server.js": "const app = require('express')()\napp.get('/healthz', (q, s) => s.send('ok'))\napp.listen(process.env.PORT || 3000)\n",
            "Dockerfile": "FROM node:20-alpine\nCOPY . .\nCMD [\"node\", \"server.js\"]\n",
            ".env.example": "NODE_ENV=production\nSESSION_SECRET=replace-me\nDATABASE_URL=mysql://u:p@h/db\n",
        }))
        self.assertEqual(a.framework, "Express")
        self.assertEqual(a.runtime, "Node.js 20")
        self.assertEqual(a.container_port, 3000)
        self.assertEqual(a.health_check_path, "/healthz")
        self.assertTrue(a.use_database)
        self.assertEqual(a.environment, {"NODE_ENV": "production"}, "비밀 이름·DATABASE_URL은 environment에 넣지 않음")

    def test_unknown_port_is_unresolved_not_guessed(self):
        a = analyze_files(files(**{
            "requirements.txt": "flask==3.0.0\n",
            "app.py": "from flask import Flask\napp = Flask(__name__)\n@app.route('/')\ndef index():\n    return 'hi'\n",
        }))
        self.assertIsNone(a.container_port)
        self.assertIn("container_port", a.unresolved)
        self.assertEqual(a.health_check_path, "/")
        self.assertIn("dockerfile", a.unresolved)

    def test_sqlite_warns_and_marks_stateful(self):
        a = analyze_files(files(**{
            "requirements.txt": "fastapi\n",
            "main.py": "import sqlite3\nconn = sqlite3.connect('app.db')\n",
            "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n",
        }))
        self.assertIs(a.use_database, False)
        self.assertTrue(a.sizing_hints["stateful_local_files"])
        self.assertTrue(any(f.level == "warn" and "SQLite" in f.title for f in a.findings))

    def test_unsupported_databases(self):
        for dep in ("pymongo", "psycopg2-binary"):
            with self.subTest(dep=dep):
                a = analyze_files(files(**{"requirements.txt": f"fastapi\n{dep}\n", "Dockerfile": "FROM python:3.12\nEXPOSE 8000\n"}))
                self.assertFalse(a.supported)
                self.assertTrue(a.unsupported_reasons)

    def test_multiple_different_images_unsupported(self):
        compose = (
            "services:\n  web:\n    build: ./web\n    ports:\n      - \"80:80\"\n"
            "  api:\n    build: ./api\n    ports:\n      - \"8000:8000\"\n"
        )
        a = analyze_files(files(**{"docker-compose.yml": compose, "web/Dockerfile": "FROM nginx\n", "api/Dockerfile": "FROM python:3.12\n"}))
        self.assertFalse(a.supported)


class SourceTests(unittest.TestCase):
    def _zip(self, entries: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_github_style_root_folder_is_removed(self):
        fs = files_from_zip(self._zip({"repo-main/app.py": "x", "repo-main/Dockerfile": "FROM python"}))
        self.assertEqual([f.path for f in fs], ["Dockerfile", "app.py"])

    def test_escaping_paths_and_noise_are_skipped(self):
        fs = files_from_zip(self._zip({
            "../evil.py": "x",
            "/etc/passwd": "x",
            "app.py": "ok",
            "node_modules/x/index.js": "x",
            "logo.png": b"\x89PNG\x00\x00",
        }))
        self.assertEqual([f.path for f in fs], ["app.py"])

    def test_bad_zip(self):
        with self.assertRaises(SourceError):
            files_from_zip(b"not a zip")


if __name__ == "__main__":
    unittest.main()
