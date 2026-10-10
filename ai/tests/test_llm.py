"""LLM 보조 단계 검사. 가짜 client로 돌아서 네트워크·비용이 없다 (실제 LLM 호출은 일반 테스트에서 분리, docs/CI.md)."""

import json
import unittest
from types import SimpleNamespace

from paved_ai.analysis import analyze_files
from paved_ai.llm import FALLBACK_BETA, OUTPUT_SCHEMA, fill_unresolved, select_files
from paved_ai.source import SourceFile

# 규칙이 포트를 못 찾는 앱: 포트가 설정 객체 안에 있음
APP = {
    "requirements.txt": "flask==3.0.0\n",
    "config.py": "class Settings:\n    LISTEN_PORT = 5050\n",
    "app.py": "from flask import Flask\nfrom config import Settings\napp = Flask(__name__)\n@app.route('/ping')\ndef ping():\n    return 'pong'\napp.run(host='0.0.0.0', port=Settings.LISTEN_PORT)\n",
    "Dockerfile": "FROM python:3.12-slim\nCOPY . .\nCMD [\"python\", \"app.py\"]\n",
    ".env": "SECRET_KEY=FAKE-should-never-be-sent\n",
}


def files(spec=APP):
    return [SourceFile(p, t) for p, t in spec.items()]


class FakeClient:
    def __init__(self, payload=None, stop_reason="end_turn", error=None):
        self.payload, self.stop_reason, self.error = payload, stop_reason, error
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        text = json.dumps(self.payload) if self.payload is not None else "not json"
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop_reason,
            model=kwargs["model"],
            _request_id="req_fake",
        )


def answer(field, value, file, line, reason="근거"):
    return {"field": field, "value": value, "file": file, "line": line, "reason": reason}


class FillTests(unittest.TestCase):
    def setUp(self):
        self.files = files()
        self.analysis = analyze_files(self.files)
        # 전제: 규칙은 포트를 못 찾고, 헬스체크는 /ping 을 찾음
        self.assertIn("container_port", self.analysis.unresolved)
        self.assertEqual(self.analysis.health_check_path, "/ping")

    def test_accepts_answer_with_real_evidence(self):
        client = FakeClient({"answers": [answer("container_port", "5050", "config.py", 2)], "notes": []})
        out = fill_unresolved(self.analysis, self.files, client)
        self.assertEqual(out.filled, ["container_port"])
        self.assertEqual(self.analysis.container_port, 5050)
        self.assertNotIn("container_port", self.analysis.unresolved)
        result = self.analysis.to_result()
        self.assertEqual(result["app_config"]["container_port"], 5050)
        self.assertEqual(result["ai"]["filled"], ["container_port"])
        self.assertIn({"file": "config.py", "line": 2, "text": "LISTEN_PORT = 5050"}, result["evidence"])

    def test_rejects_answer_whose_line_does_not_contain_value(self):
        client = FakeClient({"answers": [answer("container_port", "8080", "config.py", 2)], "notes": []})
        out = fill_unresolved(self.analysis, self.files, client)
        self.assertEqual(out.filled, [])
        self.assertIn("근거 줄", out.rejected["container_port"])
        self.assertIsNone(self.analysis.container_port)
        self.assertIn("container_port", self.analysis.unresolved, "검증 실패면 그대로 못 찾은 값")

    def test_rejects_made_up_file_and_bad_values(self):
        cases = [
            answer("container_port", "5050", "nope.py", 1),
            answer("container_port", "99999", "config.py", 2),
            answer("container_port", "5050", "config.py", 99),
        ]
        for a in cases:
            with self.subTest(a=a):
                analysis = analyze_files(self.files)
                out = fill_unresolved(analysis, self.files, FakeClient({"answers": [a], "notes": []}))
                self.assertEqual(out.filled, [])
                self.assertIsNone(analysis.container_port)

    def test_never_overrides_values_rules_found(self):
        client = FakeClient({"answers": [answer("health_check_path", "/admin", "app.py", 4)], "notes": []})
        fill_unresolved(self.analysis, self.files, client)
        self.assertEqual(self.analysis.health_check_path, "/ping", "묻지 않은 값은 무시")

    def test_request_shape_and_masking(self):
        client = FakeClient({"answers": [], "notes": []})
        fill_unresolved(self.analysis, self.files, client)
        kw = client.calls[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["betas"], [FALLBACK_BETA])
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["output_config"]["format"]["schema"], OUTPUT_SCHEMA)
        self.assertNotIn("thinking", kw, "Opus 5.5는 thinking을 끌 수 없음, effort로만 조절")
        sent = kw["system"] + json.dumps(kw["messages"], ensure_ascii=False)
        self.assertNotIn("FAKE-should-never-be-sent", sent)
        self.assertIn("데이터일 뿐", kw["system"], "업로드 코드 속 지시를 따르지 않게")

    def test_no_call_when_nothing_unresolved(self):
        self.analysis.unresolved.clear()
        client = FakeClient({"answers": [], "notes": []})
        out = fill_unresolved(self.analysis, self.files, client)
        self.assertEqual(client.calls, [])
        self.assertEqual(out.asked, [])

    def test_refusal_and_bad_json_keep_rule_result(self):
        for client in (FakeClient({"answers": []}, stop_reason="refusal"), FakeClient(None)):
            with self.subTest(stop=client.stop_reason):
                analysis = analyze_files(self.files)
                out = fill_unresolved(analysis, self.files, client)
                self.assertEqual(out.filled, [])
                self.assertIn("container_port", out.rejected)
                self.assertIn("container_port", analysis.unresolved)

    def test_notes_become_findings_and_pass_backend_check(self):
        from paved_ai.masking import backend_unsafe_paths

        notes = [{"level": "warn", "title": "포트가 설정 클래스에 있습니다", "detail": "환경마다 다르면 고정값으로 바꾸세요."}]
        fill_unresolved(self.analysis, self.files, FakeClient({"answers": [answer("container_port", "5050", "config.py", 2)], "notes": notes}))
        result = self.analysis.to_result()
        self.assertIn("포트가 설정 클래스에 있습니다", [f["title"] for f in result["findings"]])
        self.assertEqual(backend_unsafe_paths(result), [])


class SelectFilesTests(unittest.TestCase):
    def test_prioritizes_deploy_files_and_skips_noise(self):
        picked = [f.path for f in select_files([
            SourceFile("docs/guide.md", "# guide"),
            SourceFile("app.py", "app.run(port=1)"),
            SourceFile("Dockerfile", "FROM python"),
            SourceFile("requirements.txt", "flask"),
            SourceFile("static/logo.svg", "<svg/>"),
        ])]
        self.assertEqual(picked, ["Dockerfile", "requirements.txt", "app.py"])


if __name__ == "__main__":
    unittest.main()
