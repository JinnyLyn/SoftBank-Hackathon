"""비밀값 경계의 중첩 문자열 회귀. 모든 비밀값은 합성 테스트 데이터다."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "back"))
from fastapi import HTTPException
from app.main import _contains_inline_secret, _redact, _redact_tree, _reject_secret_fields


SENTINEL = "SYNTHETIC_CI_SECRET"
SECRET_TEXTS = (
    f"environment: API_KEY={SENTINEL}",
    f'message="PASSWORD={SENTINEL}"',
    f'config={{"password":"{SENTINEL}"}}',
    f"description: apiKey={SENTINEL}",
    f'config={{"awsSecretAccessKey":"{SENTINEL}"}}',
    f"config: DB_PASSWORD='{SENTINEL} with spaces'",
    f"outer=middle=access-token={SENTINEL}",
    f"API_KEY={SENTINEL}, message='password={SENTINEL}'",
    "config=" + json.dumps(json.dumps({"password": SENTINEL})),
    f'PASSWORD={{"value":"{SENTINEL}"}}',
    f'PASSWORD=["{SENTINEL}"]',
)


class SecretBoundaryTests(unittest.TestCase):
    def test_nested_assignments_are_rejected(self):
        for text in SECRET_TEXTS:
            with self.subTest(text=text):
                self.assertTrue(_contains_inline_secret(text))
                with self.assertRaises(HTTPException) as raised:
                    _reject_secret_fields({"description": [text]})
                self.assertEqual(raised.exception.status_code, 422)
                self.assertNotIn(SENTINEL, raised.exception.detail)

    def test_nested_assignments_are_redacted_in_messages_and_details(self):
        for text in SECRET_TEXTS:
            with self.subTest(text=text):
                self.assertNotIn(SENTINEL, _redact(text))
                self.assertNotIn(SENTINEL, json.dumps(_redact_tree({"description": [text]})))
        self.assertEqual(_redact(f'message="PASSWORD={SENTINEL}"'), 'message="PASSWORD=[REDACTED]"')
        self.assertEqual(_redact(f'config={{"password":"{SENTINEL}"}}'), 'config={"password":"[REDACTED]"}')

    def test_docker_tags_and_ordinary_key_values_are_preserved(self):
        for text in ("python:3.12", "image: python:3.12", "image=registry.test/team/api:3.13",
                     'config={"port":8000}', "tokenizer=normal", "message=ordinary"):
            with self.subTest(text=text):
                self.assertFalse(_contains_inline_secret(text))
                _reject_secret_fields({"description": text})
                self.assertEqual(_redact(text), text)

    def test_existing_secret_forms_remain_protected(self):
        for key in ("PASSWORD", "db_password", "access-token", "apiKey", "APIKey", "privateKey"):
            with self.subTest(key=key):
                with self.assertRaises(HTTPException):
                    _reject_secret_fields({key: SENTINEL})
                self.assertEqual(_redact_tree({key: SENTINEL}), {key: "[REDACTED]"})
        for text in (f"mysql://user:{SENTINEL}@db/test", f"Bearer {SENTINEL}", "AKIAABCDEFGHIJKLMNOP"):
            with self.subTest(text=text):
                with self.assertRaises(HTTPException):
                    _reject_secret_fields(text)
                self.assertNotIn(text, _redact(text))


if __name__ == "__main__":
    unittest.main()
