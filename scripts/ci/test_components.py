"""검사 대상의 누락과 선행 작업 실패가 성공으로 바뀌지 않는지 확인한다."""

import copy
import io
from pathlib import Path
import tempfile
import unittest

from components import CONTRACTS, inspect, report
from test_platform_back import failure_diagnostics


class FailureDiagnosticsTests(unittest.TestCase):
    def test_reports_traceback_location_and_type_without_values(self):
        log = io.BytesIO(b'Traceback (most recent call last):\n'
                        b'  File "/private/backend/app/main.py", line 42, in startup\n'
                        b'    raise RuntimeError("fake-secret-sentinel")\n'
                        b'RuntimeError: mysql://user:fake-password@host/db\n'
                        b'ANTHROPIC_API_KEY=fake-key-sentinel\n')
        result = failure_diagnostics(log)
        self.assertIn("main.py:42", result)
        self.assertIn("RuntimeError", result)
        for secret in ("fake-secret-sentinel", "fake-password", "fake-key-sentinel", "/private", "mysql://"):
            self.assertNotIn(secret, result)

    def test_limits_input_tail_and_diagnostic_lines(self):
        log = io.BytesIO(b'ValueError: old\n' + b'x' * 20_000 + b'\n' + b'RuntimeError: new\n' * 100)
        result = failure_diagnostics(log)
        self.assertNotIn("ValueError", result)
        self.assertEqual(result.count("RuntimeError"), 12)

    def test_redacts_unstructured_errors_and_control_characters(self):
        log = io.BytesIO(b'ERROR: fake-secret-sentinel\n'
                        b'  File "/app/\x1b[31mmain.py", line 2\n')
        result = failure_diagnostics(log)
        self.assertIn("Uvicorn ERROR", result)
        self.assertNotIn("fake-secret-sentinel", result)
        self.assertNotIn("\x1b", result)

    def test_handles_empty_log(self):
        self.assertIn("traceback 없음", failure_diagnostics(io.BytesIO()))


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.add_component("sample_front")

    def add_component(self, key):
        directory, files = CONTRACTS[key]
        for file in files:
            path = self.root / directory / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_empty_scaffolds_are_explicitly_absent(self):
        (self.root / "back").mkdir()
        (self.root / "back/.gitkeep").touch()
        self.assertEqual(inspect(self.root), dict(sample_front=True, sample_back=False, front=False, back=False, infra=False))

    def test_new_component_cannot_silently_skip_missing_contract(self):
        (self.root / "front").mkdir()
        (self.root / "front/app.js").touch()
        with self.assertRaisesRegex(ValueError, "front:.*누락"):
            inspect(self.root)

    def test_existing_sample_cannot_disappear(self):
        (self.root / "sample-front/app.js").unlink()
        with self.assertRaisesRegex(ValueError, "sample-front"):
            inspect(self.root)

    def test_previously_merged_component_cannot_disappear(self):
        for key in ("front", "back"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, f"{key}: 기존 구현이 사라졌습니다"):
                    inspect(self.root, previously_present={key})

    def test_merged_components_activate(self):
        for key in CONTRACTS:
            self.add_component(key)
        self.assertTrue(all(inspect(self.root).values()))

    def test_backend_without_execution_contract_fails(self):
        (self.root / "back").mkdir()
        (self.root / "back/main.py").touch()
        with self.assertRaisesRegex(ValueError, "back:.*누락"):
            inspect(self.root)

    def test_backend_database_contract_files_cannot_disappear(self):
        for filename in ("Dockerfile", "app/cli.py", "app/migrations.py",
                         "migrations/004_deployment_rollbacks.sql", "migrations/005_rollback_plan_approval.sql"):
            with self.subTest(filename=filename):
                self.add_component("back")
                (self.root / "back" / filename).unlink()
                with self.assertRaisesRegex(ValueError, "back:.*누락"):
                    inspect(self.root)

    def test_backend_activates_without_frontend_or_infrastructure(self):
        self.add_component("back")
        present = inspect(self.root)
        self.assertTrue(present["back"])
        self.assertFalse(present["front"])
        self.assertFalse(present["infra"])

    def test_backend_success_describes_database_scope_without_claiming_deployment(self):
        needs = {key: {"result": "skipped"} for key in CONTRACTS}
        needs["sample_front"]["result"] = "success"
        needs["back"] = {"result": "success"}
        needs["inventory"] = {
            "result": "success",
            "outputs": {key: str(key in ("sample_front", "back")).lower() for key in CONTRACTS},
        }
        failed, summary = report(needs)
        self.assertFalse(failed)
        self.assertIn("MySQL 8.4·마이그레이션 재실행·롤백 API 계약", summary)
        self.assertIn("프런트·LLM·실제 AWS 배포는 미검증", summary)
        self.assertIn("전체 플랫폼 통합: 미검증", summary)

    def test_aggregate_propagates_every_unexpected_result(self):
        needs = {key: {"result": "success"} for key in CONTRACTS}
        needs["inventory"] = {"result": "success", "outputs": {key: "true" for key in CONTRACTS}}
        self.assertFalse(report(needs)[0])
        for key in needs:
            for result in ("failure", "cancelled", "skipped", "missing"):
                with self.subTest(key=key, result=result):
                    broken = copy.deepcopy(needs)
                    broken[key]["result"] = result
                    self.assertTrue(report(broken)[0])

    def test_only_explicit_absence_allows_skipped_job(self):
        needs = {key: {"result": "skipped"} for key in CONTRACTS}
        needs["sample_front"]["result"] = "success"
        needs["inventory"] = {"result": "success", "outputs": {key: "false" for key in CONTRACTS}}
        needs["inventory"]["outputs"]["sample_front"] = "true"
        failed, summary = report(needs)
        self.assertFalse(failed)
        self.assertIn("전체 플랫폼 통합: 미검증", summary)
        invalid = copy.deepcopy(needs)
        invalid["inventory"]["outputs"]["sample_front"] = "false"
        self.assertTrue(report(invalid)[0])
        needs["inventory"]["outputs"].pop("infra")
        self.assertTrue(report(needs)[0])


if __name__ == "__main__":
    unittest.main()
