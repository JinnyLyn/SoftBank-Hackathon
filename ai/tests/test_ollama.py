"""Ollama 경로 검사. 가짜 client와 로컬 가짜 서버로 돌아서 네트워크·비용이 없다."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import runner
from paved_ai.analysis import analyze_files
from paved_ai.llm import LlmUnavailable, fill_unresolved
from paved_ai.ollama import OllamaClient, Reply

from tests.test_llm import answer, files


class FakeOllama(OllamaClient):
    def __init__(self, text, stop_reason="end_turn"):
        super().__init__("fake-key")
        self.text, self.stop_reason, self.calls = text, stop_reason, []

    def chat(self, model, system, user, schema, max_tokens):
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        return Reply(self.text, self.stop_reason, model, "req_fake")


class OllamaFillTests(unittest.TestCase):
    def setUp(self):
        self.files = files()
        self.analysis = analyze_files(self.files)

    def test_accepts_fenced_json_and_skips_answers_missing_fields(self):
        # glm-5.3 실제 응답처럼 코드 블록으로 감싸고, 한 답은 근거 파일을 빼먹음
        payload = {"answers": [answer("container_port", "5050", "config.py", 2), {"field": "framework", "value": "Flask"}],
                   "notes": ["문자열 메모는 무시"]}
        client = FakeOllama("```json\n" + json.dumps(payload) + "\n```")
        out = fill_unresolved(self.analysis, self.files, client, model="glm-5.3")
        self.assertEqual(out.filled, ["container_port"])
        self.assertEqual(self.analysis.container_port, 5050)
        self.assertEqual(out.model, "glm-5.3")
        self.assertIn("JSON 스키마", client.calls[0]["system"], "스키마를 강제하지 않으므로 형식을 프롬프트에도 적음")
        self.assertNotIn("FAKE-should-never-be-sent", client.calls[0]["user"])

    def test_text_around_json_and_non_json(self):
        payload = {"answers": [answer("container_port", "5050", "config.py", 2)], "notes": []}
        out = fill_unresolved(self.analysis, self.files, FakeOllama("답입니다: " + json.dumps(payload) + " 끝"))
        self.assertEqual(out.filled, ["container_port"])

        analysis = analyze_files(self.files)
        out = fill_unresolved(analysis, self.files, FakeOllama("모르겠습니다"))
        self.assertEqual(out.filled, [])
        self.assertEqual(out.rejected["container_port"], "LLM 답이 JSON이 아님")
        out = fill_unresolved(analyze_files(self.files), self.files, FakeOllama("[1, 2]"))
        self.assertEqual(out.rejected["container_port"], "LLM 답이 JSON이 아님")

    def test_length_cut_is_rejected(self):
        out = fill_unresolved(self.analysis, self.files, FakeOllama('{"answers": [', stop_reason="max_tokens"))
        self.assertEqual(out.rejected["container_port"], "답이 길이 제한에 걸림")


class _Handler(BaseHTTPRequestHandler):
    status, body, seen = 200, {}, []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                              "body": json.loads(self.rfile.read(length))})
        data = json.dumps(_Handler.body).encode()
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class OllamaHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.seen.clear()

    def test_request_shape_and_reply(self):
        _Handler.status, _Handler.body = 200, {"model": "glm-5.3", "message": {"content": "{}", "thinking": "..."}, "done_reason": "length"}
        reply = OllamaClient("k-test", base_url=self.url).chat("glm-5.3", "sys", "user", {"type": "object"}, 100)
        self.assertEqual((reply.text, reply.stop_reason, reply.model), ("{}", "max_tokens", "glm-5.3"))
        sent = _Handler.seen[0]
        self.assertEqual(sent["path"], "/api/chat")
        self.assertEqual(sent["auth"], "Bearer k-test")
        self.assertEqual(sent["body"]["stream"], False)
        self.assertEqual(sent["body"]["format"], {"type": "object"})
        self.assertEqual([m["role"] for m in sent["body"]["messages"]], ["system", "user"])

    def test_errors_become_unavailable_without_key(self):
        a, fs = analyze_files(files()), files()
        for status, body, expect in [(401, {"error": "unauthorized"}, "OLLAMA_API_KEY"),
                                     (404, {"error": "model 'glm-9' not found"}, "model 'glm-9' not found")]:
            _Handler.status, _Handler.body = status, body
            with self.subTest(status=status), self.assertRaises(LlmUnavailable) as ctx:
                fill_unresolved(a, fs, OllamaClient("k-secret-value", base_url=self.url), model="glm-9")
            self.assertIn(expect, str(ctx.exception))
            self.assertNotIn("k-secret-value", str(ctx.exception))
        self.assertNotIn("k-secret-value", repr(OllamaClient("k-secret-value")))


class ProviderChoiceTests(unittest.TestCase):
    def pick(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return runner.make_llm_client()

    def test_ollama_first_then_anthropic(self):
        client, model = self.pick({"OLLAMA_API_KEY": "x", "ANTHROPIC_API_KEY": "y"})
        self.assertIsInstance(client, OllamaClient)
        self.assertEqual(model, "glm-5.3")
        self.assertEqual(self.pick({}), (None, None))
        client, _ = self.pick({"PAVED_AI_PROVIDER": "ollama"})
        self.assertIsNone(client, "Ollama를 고르고 키가 없으면 규칙 분석만")
        client, _ = self.pick({"PAVED_AI_PROVIDER": "nope", "OLLAMA_API_KEY": "x"})
        self.assertIsNone(client)

    def test_no_llm_flag(self):
        with mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "x"}, clear=True):
            self.assertEqual(runner.make_llm_client(enabled=False), (None, None))


if __name__ == "__main__":
    unittest.main()
