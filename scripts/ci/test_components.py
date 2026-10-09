"""검사 대상의 누락과 선행 작업 실패가 성공으로 바뀌지 않는지 확인한다."""

import copy
from pathlib import Path
import tempfile
import unittest

from components import CONTRACTS, inspect, report


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
        self.assertEqual(inspect(self.root), dict(sample_front=True, sample_back=False, front=False, infra=False))

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
        with self.assertRaisesRegex(ValueError, "front: 기존 구현이 사라졌습니다"):
            inspect(self.root, previously_present={"front"})

    def test_merged_components_activate(self):
        for key in CONTRACTS:
            self.add_component(key)
        self.assertTrue(all(inspect(self.root).values()))

    def test_new_platform_backend_requires_integration_check(self):
        (self.root / "back").mkdir()
        (self.root / "back/main.py").touch()
        with self.assertRaisesRegex(ValueError, "실제 실행·통합 검사"):
            inspect(self.root)

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
