"""비밀값 가리기 검사. 테스트용 값은 모두 가짜다."""

import re
import unittest
from pathlib import Path

from paved_ai import masking
from paved_ai.masking import backend_safe_text, backend_unsafe_paths, mask_files, mask_text
from paved_ai.source import SourceFile

BACKEND_MAIN = next(
    (p / "back/app/main.py" for p in Path(__file__).resolve().parents if (p / "back/app/main.py").is_file()), None
)


class MaskTextTests(unittest.TestCase):
    def test_shapes_are_masked_anywhere(self):
        fake = {
            "aws": "AKIAABCDEFGHIJKLMNOP",
            "anthropic": "sk-ant-api03-FAKEFAKEFAKEFAKE",
            "github": "ghp_FAKEFAKEFAKEFAKEFAKEFAKE1234",
            "url": "mysql://admin:FAKE-pass@db.example.com/app",
            "bearer": "Authorization: Bearer abcdefghijklmnop",
            "pem": "-----BEGIN PRIVATE KEY-----\nMIIfake\n-----END PRIVATE KEY-----",
        }
        for name, value in fake.items():
            with self.subTest(name=name):
                out, n = mask_text(f"x = '{value}'")
                self.assertGreater(n, 0)
                self.assertNotIn("FAKE", out.replace("<가림>", ""))
        out, _ = mask_text("mysql://admin:FAKE-pass@db.example.com/app")
        self.assertEqual(out, "mysql://<가림>@db.example.com/app", "호스트는 남기고 아이디:비밀번호만 가림")

    def test_config_values_masked_but_names_kept(self):
        out, n = mask_text("APP_ENV=production\nJWT_SECRET=abc123\n- MYSQL_ROOT_PASSWORD=pw\nAPI_KEY: \"k\"\n", config_file=True)
        self.assertEqual(n, 3)
        self.assertIn("APP_ENV=production", out)
        self.assertIn("JWT_SECRET=<가림>", out)
        self.assertIn("MYSQL_ROOT_PASSWORD=<가림>", out)

    def test_code_keeps_variable_names(self):
        code = 'SECRET_KEY = os.getenv("SECRET_KEY")\npassword = request.form["password"]\nSTRIPE_API_KEY = "sk_live_fakefake"\n'
        out, n = mask_text(code)
        self.assertIn('SECRET_KEY = os.getenv("SECRET_KEY")', out, "환경 변수에서 읽는 줄은 그대로")
        self.assertIn('password = request.form["password"]', out)
        self.assertIn('STRIPE_API_KEY = "<가림>"', out, "문자열로 박아 둔 값만 가림")
        self.assertEqual(n, 1)


    def test_short_secret_names_and_quoted_keys(self):
        # PR #25 리뷰: DB_PASS 같은 줄임 이름과 JSON의 따옴표 키가 그대로 LLM에 갔음
        for text, config in [
            ("DB_PASS: hunter2", True),
            ('  "DB_PASSWORD": "hunter2",', True),
            ('{"DB_PASSWORD": "hunter2"}', False),
            ('{"auth": "tok123456"}', False),
            ('db_pass="hunter22"', False),
            ("SENTRY_DSN=https://k@o1.ingest.sentry.io/1", True),
            ("MYSQL_PWD=hunter2", True),
        ]:
            with self.subTest(text=text):
                masked, n = mask_text(text, config_file=config)
                self.assertGreater(n, 0)
                self.assertNotIn("hunter2", masked)
                self.assertNotIn("tok123456", masked)

    def test_similar_words_are_not_secrets(self):
        for text in ("PORT: 8080", "passenger_count: 3", '"listen": 7311'):
            with self.subTest(text=text):
                self.assertEqual(mask_text(text, config_file=True), (text, 0))
        # 코드의 변수 이름은 지킴 (설정 파일 규칙은 코드에 쓰지 않음)
        for text in ('author: "kim"', 'SECRET_KEY = os.getenv("SECRET_KEY")'):
            with self.subTest(text=text):
                self.assertEqual(mask_text(text), (text, 0))


class MaskFilesTests(unittest.TestCase):
    def test_secret_files_withheld(self):
        report = mask_files([
            SourceFile(".env", "DATABASE_URL=mysql://u:FAKE@h/db\nDEBUG=1\n"),
            SourceFile("deploy/id_rsa", "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"),
            SourceFile(".env.example", "DEBUG=1\n"),
            SourceFile("app.py", "print('hi')\n"),
        ])
        self.assertEqual(report.withheld, [".env", "deploy/id_rsa"])
        by_path = {f.path: f.text for f in report.files}
        self.assertEqual(by_path[".env"], "DATABASE_URL=<가림>\nDEBUG=<가림>", ".env는 이름만")
        self.assertNotIn("deploy/id_rsa", by_path, "개인 키는 아예 보내지 않음")
        self.assertEqual(by_path[".env.example"], "DEBUG=1\n", "예시 파일은 그대로 분석")

    def test_original_files_unchanged(self):
        original = [SourceFile("settings.yml", "API_TOKEN: real-looking-value\n")]
        mask_files(original)
        self.assertEqual(original[0].text, "API_TOKEN: real-looking-value\n")


class BackendGuardTests(unittest.TestCase):
    def test_masked_assignment_would_still_be_rejected_so_we_rewrite(self):
        self.assertTrue(backend_unsafe_paths({"x": "SECRET_KEY=<가림>"}), "백엔드는 가린 값도 거절함")
        safe = backend_safe_text("SECRET_KEY=<가림>")
        self.assertEqual(safe, "SECRET_KEY (값 가림)")
        self.assertEqual(backend_unsafe_paths({"x": safe}), [])

    def test_nested_and_json_string_assignments(self):
        # 백엔드가 Issue #16 에서 새로 막은 모양: 바깥 이름이 평범해도 안쪽 비밀 이름을 봄
        for text in ('environment: API_KEY=<가림>', '{"log": "{\\"API_KEY\\": \\"<가림>\\"}"}'):
            with self.subTest(text=text):
                self.assertTrue(backend_unsafe_paths({"x": text}))
                self.assertEqual(backend_unsafe_paths({"x": backend_safe_text(text)}), [])
        self.assertEqual(backend_unsafe_paths({"x": "FROM python:3.12"}), [], "일반 Docker 태그는 허용")

    def test_field_names_checked(self):
        self.assertTrue(backend_unsafe_paths({"api_key": "x"}))
        self.assertEqual(backend_unsafe_paths({"masking": {"withheld_files": [], "redactions": 1}}), [])

    @unittest.skipIf(BACKEND_MAIN is None, "back/app/main.py 없음")
    def test_rules_match_backend_source(self):
        """백엔드 규칙이 바뀌면 이 테스트가 깨짐 → masking.py 의 BACKEND_* 를 같이 고침"""
        src = BACKEND_MAIN.read_text(encoding="utf-8")
        pairs = {
            "_SECRET_KEY": masking.BACKEND_SECRET_KEY,
            "_KEY_ASSIGNMENT": masking.BACKEND_KEY_ASSIGNMENT,
            "_ASSIGNMENT_VALUE": masking.BACKEND_ASSIGNMENT_VALUE,
            "_CREDENTIAL_URL": masking.BACKEND_CREDENTIAL_URL,
            "_BEARER": masking.BACKEND_BEARER,
            "_AWS_ACCESS_KEY": masking.BACKEND_AWS_ACCESS_KEY,
        }
        for name, ours in pairs.items():
            with self.subTest(name=name):
                m = re.search(rf"^{name} = re\.compile\(\s*(r(?:\"\"\"[\s\S]*?\"\"\"|\"[^\"\n]*\"))\s*\)", src, re.M)
                self.assertIsNotNone(m, f"백엔드에서 {name}을 찾지 못함")
                self.assertEqual(eval(m.group(1)), ours.pattern)  # noqa: S307 (저장소 안 파일의 정규식 문자열)


if __name__ == "__main__":
    unittest.main()
