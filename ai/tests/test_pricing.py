"""가격표와 월 비용 계산 검사. 외부 서비스 없이 표준 라이브러리만으로 돈다."""

import json
import re
import unittest
from decimal import Decimal
from pathlib import Path

from paved_ai.pricing import (
    MAX_TASKS_RANGE,
    MIN_TASKS_ALLOWED,
    PricingError,
    estimate_app_cost,
    load_prices,
)

# 저장소 루트의 infra 모듈. back/app/llm/ 으로 옮겨도 위로 올라가며 찾음
MODULE_DIR = next(
    (p / "infra/modules/ecs-web-app" for p in Path(__file__).resolve().parents if (p / "infra/modules/ecs-web-app").is_dir()),
    None,
)


class EstimateTests(unittest.TestCase):
    # 손으로 계산한 값: (vCPU × 0.04656 + GB × 0.00511) × 730
    def test_x86_sizes_per_task(self):
        cases = {"xsmall": "10.36", "small": "20.72", "medium": "41.45"}
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(estimate_app_cost(size, 1, 1).per_task_monthly, Decimal(expected))

    def test_task_count_multiplies_before_rounding(self):
        # medium 1개 41.4494… → 2개 82.8988 → 82.90, 4개 165.7976 → 165.80
        cost = estimate_app_cost("medium", 2, 4)
        self.assertEqual(cost.min_monthly, Decimal("82.90"))
        self.assertEqual(cost.max_monthly, Decimal("165.80"))
        # xsmall 10.36235 × 3 = 31.08705 → 31.09 (먼저 반올림했다면 31.08)
        self.assertEqual(estimate_app_cost("xsmall", 1, 3).max_monthly, Decimal("31.09"))

    def test_arm_is_cheaper(self):
        self.assertLess(
            estimate_app_cost("small", 1, 1, "ARM64").per_task_monthly,
            estimate_app_cost("small", 1, 1, "X86_64").per_task_monthly,
        )

    def test_budget_uses_max_tasks(self):
        cost = estimate_app_cost("small", 1, 2)  # 최소 20.72, 최대 41.45
        self.assertTrue(cost.fits_budget(50))
        self.assertFalse(cost.fits_budget(30), "자동 확장 최대치가 예산을 넘으면 예산 초과")
        self.assertTrue(cost.fits_budget("41.45"), "경계값은 예산 안")

    def test_plan_cost_estimate_matches_backend_schema(self):
        est = estimate_app_cost("small", 1, 2).to_plan_cost_estimate()
        # back/app/schemas.py CostEstimate: amount ≥0 소수 4자리 이내, currency 대문자 3자, period month, pricing_as_of 4~32자
        self.assertEqual(set(est), {"amount", "currency", "period", "pricing_as_of"})
        self.assertRegex(est["amount"], r"^\d+\.\d{2}$")
        self.assertEqual(est["amount"], "41.45")
        self.assertRegex(est["currency"], r"^[A-Z]{3}$")
        self.assertEqual(est["period"], "month")
        self.assertTrue(4 <= len(est["pricing_as_of"]) <= 32)


class InvalidInputTests(unittest.TestCase):
    """LLM이 형식 밖 값을 내도 여기서 걸러져야 함."""

    def test_rejects_out_of_range(self):
        bad = [
            ("large", 1, 1),  # 없는 크기
            ("small", 0, 1),  # 최소 작업 수 0
            ("small", 3, 4),  # 최소 작업 수 3 (규칙은 1, 2)
            ("small", 1, 5),  # 최대 작업 수 5
            ("small", 2, 1),  # 최대 < 최소
            ("small", True, 1),  # bool은 숫자로 보지 않음
            ("small", 1, 2.0),  # 실수
        ]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(PricingError):
                    estimate_app_cost(*args)

    def test_rejects_unknown_architecture(self):
        with self.assertRaises(PricingError):
            estimate_app_cost("small", 1, 1, "RISCV")


class PriceTableTests(unittest.TestCase):
    def test_price_table_is_complete(self):
        p = load_prices()
        self.assertEqual(p.region, "ap-northeast-2")
        self.assertEqual(p.currency, "USD")
        self.assertEqual(set(p.fargate), {"X86_64", "ARM64"})
        for arch, rate in p.fargate.items():
            with self.subTest(arch=arch):
                self.assertGreater(rate["vcpu_hour"], 0)
                self.assertGreater(rate["gb_hour"], 0)
        self.assertTrue(p.excluded, "제외 항목을 화면에 밝혀야 함")


@unittest.skipIf(MODULE_DIR is None, "infra/modules/ecs-web-app 이 없는 위치에서 실행됨")
class InfraSyncTests(unittest.TestCase):
    """가격표가 Terraform 모듈·LLM 출력 스키마와 어긋나지 않는지. 한쪽을 바꾸면 이 테스트가 깨짐."""

    def test_task_sizes_match_module(self):
        main_tf = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        module_sizes = {
            name: (int(cpu), int(mem))
            for name, cpu, mem in re.findall(r"(\w+)\s*=\s*\{\s*cpu\s*=\s*(\d+),\s*memory\s*=\s*(\d+)\s*\}", main_tf)
        }
        self.assertTrue(module_sizes, "main.tf에서 task_sizes를 찾지 못함")
        table = {
            name: (int(v["vcpu"] * 1024), int(v["memory_gb"] * 1024)) for name, v in load_prices().task_sizes.items()
        }
        self.assertEqual(table, module_sizes)

    def test_rules_match_llm_schema(self):
        schema = json.loads((MODULE_DIR / "app-config.schema.json").read_text(encoding="utf-8"))
        props = schema["properties"]
        self.assertEqual(set(props["task_size"]["enum"]), set(load_prices().task_sizes))
        self.assertEqual(tuple(props["min_tasks"]["enum"]), MIN_TASKS_ALLOWED)
        self.assertEqual((props["max_tasks"]["minimum"], props["max_tasks"]["maximum"]), MAX_TASKS_RANGE)

    def test_architectures_match_module(self):
        variables_tf = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")
        block = variables_tf[variables_tf.index('variable "cpu_architecture"') :]
        block = block[: block.index("\n}\n")]
        for arch in load_prices().fargate:
            with self.subTest(arch=arch):
                self.assertIn(arch, block)


if __name__ == "__main__":
    unittest.main()
