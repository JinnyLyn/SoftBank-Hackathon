"""비밀값 경계의 중첩 문자열 회귀. 모든 비밀값은 합성 테스트 데이터다."""

import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "back"))
from fastapi import HTTPException
from app.main import _CREDENTIAL_URL, _contains_inline_secret, _redact, _redact_tree, _reject_secret_fields


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
    # 닫는 따옴표가 없는 값(길이로 잘린 로그 줄), 줄임 이름(PASS·PWD·credentials), 이스케이프된 따옴표
    f'PASSWORD="{SENTINEL}',
    f'config={{"password": "{SENTINEL}',
    f"description: password: '{SENTINEL} with spaces",
    f"DB_PASS={SENTINEL}",
    f"MYSQL_PWD={SENTINEL}",
    f"db_credentials: {SENTINEL}",
    f'password=\\"{SENTINEL}\\"',
)
# 비밀번호가 든 주소: mysql·http(s) 말고도 모든 scheme
CREDENTIAL_URL_TEXTS = (
    f"mysql://root:{SENTINEL}@db/app",
    f"postgres://user:{SENTINEL}@db:5432/app",
    f"redis://:{SENTINEL}@cache:6379/0",
    f"mongodb://user:{SENTINEL}@mongo/app",
    f"amqp://guest:{SENTINEL}@mq",
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

    def test_credential_urls_of_any_scheme_are_rejected_and_redacted(self):
        for text in CREDENTIAL_URL_TEXTS:
            with self.subTest(text=text):
                with self.assertRaises(HTTPException) as raised:
                    _reject_secret_fields({"description": text})
                self.assertEqual(raised.exception.status_code, 422)
                self.assertNotIn(SENTINEL, raised.exception.detail)
                self.assertNotIn(SENTINEL, _redact(text))
                self.assertIn("[REDACTED]@", _redact(text))

    def test_credential_url_pattern_stays_fast_on_long_adversarial_input(self):
        # scheme 길이에 상한이 없으면 "a-" 반복처럼 :// 가 없는 긴 입력에서 O(n²)로 느려진다(4만 자에 6초). /api/plans의 variables는 최대 64KiB다
        for unit in ("a-", "a.", "a+", "a1"):
            text = unit * 20_000
            with self.subTest(unit=unit):
                started = time.perf_counter()
                self.assertIsNone(_CREDENTIAL_URL.search(text))
                self.assertLess(time.perf_counter() - started, 0.5, "정규식 검색이 입력 길이에 제곱으로 느려진다")
        self.assertIsNotNone(_CREDENTIAL_URL.search("amqp+ssl://user:pw@mq"))   # 상한 안의 scheme은 그대로 잡는다

    def test_unterminated_quote_redacts_the_rest_of_the_text(self):
        self.assertEqual(_redact(f'PASSWORD="{SENTINEL}'), "PASSWORD=[REDACTED]")
        self.assertEqual(_redact(f"password: '{SENTINEL} with spaces"), "password: [REDACTED]")
        self.assertEqual(_redact(f'password=\\"{SENTINEL}\\"'), "password=[REDACTED]")
        # 닫는 따옴표가 있는 값은 그대로 따옴표를 지킨다
        self.assertEqual(_redact(f'PASSWORD="{SENTINEL}" ok'), 'PASSWORD="[REDACTED]" ok')

    def test_docker_tags_and_ordinary_key_values_are_preserved(self):
        for text in ("python:3.12", "image: python:3.12", "image=registry.test/team/api:3.13",
                     'config={"port":8000}', "tokenizer=normal", "message=ordinary",
                     "passenger=3", "bypass=1", "compass=north", "https://example.com:8080/path",
                     "http://localhost:3000/api", "FROM python:3.13.16-slim"):
            with self.subTest(text=text):
                self.assertFalse(_contains_inline_secret(text))
                _reject_secret_fields({"description": text})
                self.assertEqual(_redact(text), text)

    def test_existing_secret_forms_remain_protected(self):
        for key in ("PASSWORD", "db_password", "access-token", "apiKey", "APIKey", "privateKey", "DB_PASS", "mysql_pwd", "credentials"):
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
