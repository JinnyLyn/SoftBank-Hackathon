"""앱 월 비용 계산.

금액은 LLM이 아니라 이 가격표로만 계산한다 (10/8 회의: 금액은 코드·가격표로).
계산 범위는 앱마다 늘어나는 Fargate 비용뿐이다. 공용 기반(ALB·RDS 등)은 shared_base로 따로 보여 준다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

PRICES_PATH = Path(__file__).with_name("prices.json")
CENT = Decimal("0.01")

# 예산 판단 기준. 자동 확장으로 최대 작업 수까지 늘어나도 예산 안이어야 추천한다 (갑작스러운 요금 방지)
BUDGET_BASIS = "max"

# app-config.schema.json의 min_tasks·max_tasks 규칙과 같게 유지 (테스트가 확인)
MIN_TASKS_ALLOWED = (1, 2)
MAX_TASKS_RANGE = (1, 4)


@dataclass(frozen=True)
class PriceTable:
    region: str
    currency: str
    pricing_as_of: str
    source: str
    hours_per_month: Decimal
    # 아키텍처("X86_64", "ARM64") → {"vcpu_hour", "gb_hour"}
    fargate: Dict[str, Dict[str, Decimal]]
    # 크기("xsmall" 등) → {"vcpu", "memory_gb"}
    task_sizes: Dict[str, Dict[str, Decimal]]
    shared_base_monthly: Decimal
    shared_base_note: str
    excluded: Tuple[str, ...]


def _decimals(d: dict) -> Dict[str, Decimal]:
    return {k: Decimal(v) for k, v in d.items()}


@lru_cache(maxsize=None)
def load_prices(path: Path = PRICES_PATH) -> PriceTable:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return PriceTable(
        region=raw["region"],
        currency=raw["currency"],
        pricing_as_of=raw["pricing_as_of"],
        source=raw["source"],
        hours_per_month=Decimal(raw["hours_per_month"]),
        fargate={arch: _decimals(v) for arch, v in raw["fargate"].items()},
        task_sizes={size: _decimals(v) for size, v in raw["task_sizes"].items()},
        shared_base_monthly=Decimal(raw["shared_base"]["monthly_usd"]),
        shared_base_note=raw["shared_base"]["note"],
        excluded=tuple(raw["excluded"]),
    )


class PricingError(ValueError):
    """가격표나 모듈 규칙에 없는 구성. LLM 출력이 잘못됐을 때 여기서 걸러진다."""


@dataclass(frozen=True)
class AppCost:
    task_size: str
    architecture: str
    min_tasks: int
    max_tasks: int
    per_task_monthly: Decimal
    min_monthly: Decimal
    max_monthly: Decimal
    currency: str
    pricing_as_of: str

    @property
    def budget_monthly(self) -> Decimal:
        """예산과 비교할 금액 (BUDGET_BASIS 기준)."""
        return self.max_monthly if BUDGET_BASIS == "max" else self.min_monthly

    def fits_budget(self, budget_usd: Union[Decimal, int, float, str]) -> bool:
        return self.budget_monthly <= Decimal(str(budget_usd))

    def to_plan_cost_estimate(self) -> dict:
        """백엔드 POST /api/plans 의 cost_estimate 형식. 화면·추천·승인이 모두 이 amount 하나를 쓴다."""
        return {
            "amount": f"{self.budget_monthly:.2f}",
            "currency": self.currency,
            "period": "month",
            "pricing_as_of": self.pricing_as_of,
        }


def _round(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def estimate_app_cost(
    task_size: str,
    min_tasks: int,
    max_tasks: int,
    architecture: str = "X86_64",
    prices: Optional[PriceTable] = None,
) -> AppCost:
    """작업 크기와 개수로 앱의 월 Fargate 비용을 계산한다.

    월 비용 = (vCPU × vCPU 시간 단가 + 메모리 GB × GB 시간 단가) × 730시간 × 작업 수
    """
    p = prices or load_prices()

    if task_size not in p.task_sizes:
        raise PricingError(f"알 수 없는 task_size: {task_size!r} (가능: {', '.join(p.task_sizes)})")
    if architecture not in p.fargate:
        raise PricingError(f"알 수 없는 architecture: {architecture!r} (가능: {', '.join(p.fargate)})")
    # bool은 int의 하위 타입이라 따로 막음
    if isinstance(min_tasks, bool) or min_tasks not in MIN_TASKS_ALLOWED:
        raise PricingError(f"min_tasks는 {MIN_TASKS_ALLOWED} 중 하나여야 합니다: {min_tasks!r}")
    lo, hi = MAX_TASKS_RANGE
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not lo <= max_tasks <= hi:
        raise PricingError(f"max_tasks는 {lo}~{hi} 사이여야 합니다: {max_tasks!r}")
    if max_tasks < min_tasks:
        raise PricingError(f"max_tasks({max_tasks})가 min_tasks({min_tasks})보다 작습니다")

    size = p.task_sizes[task_size]
    rate = p.fargate[architecture]
    per_task = (size["vcpu"] * rate["vcpu_hour"] + size["memory_gb"] * rate["gb_hour"]) * p.hours_per_month

    # 반올림은 마지막에 한 번만 (작업 수를 곱하기 전에 반올림하면 오차가 커짐)
    return AppCost(
        task_size=task_size,
        architecture=architecture,
        min_tasks=min_tasks,
        max_tasks=max_tasks,
        per_task_monthly=_round(per_task),
        min_monthly=_round(per_task * min_tasks),
        max_monthly=_round(per_task * max_tasks),
        currency=p.currency,
        pricing_as_of=p.pricing_as_of,
    )
