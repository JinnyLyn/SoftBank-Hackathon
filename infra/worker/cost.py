"""예산·사용 규모에서 구성 단계(lean/balanced/roomy)와 월 비용을 계산한다. 표준 라이브러리만 쓴다.

비용은 LLM이 아니라 이 코드가 가격표(prices.json)로 계산한다(AGENTS.md 5장 6단계).
가격표의 리전·기준일·제외 항목을 결과에 함께 담아, 제외한 비용까지 포함한 총액처럼 보이지 않게 한다(AGENTS.md 6장).
task_size 프리셋(CPU·메모리)은 modules/ecs-web-app/main.tf의 표와 같아야 한다.

월 예산이 있으면 그 금액을 넘지 않는 범위에서 최저·평균·최대 세 안을 만든다(budget_configs). 예산 판정은 부하가 최대일 때
(오토스케일링이 max_tasks까지 늘었을 때)의 월 비용(peak_monthly)으로 한다. 태스크 수·크기에 코드로 박아 둔 상한은 없고,
한도는 사용자가 정한 예산이다. 예산이 없으면 TIERS 프리셋을 그대로 쓴다.
"""
import json
from pathlib import Path

HOURS_DEFAULT = 730

# modules/ecs-web-app/main.tf 의 size 프리셋과 같은 값(vCPU, GB)
SIZES = {"xsmall": (0.25, 0.5), "small": (0.5, 1.0), "medium": (1.0, 2.0), "large": (2.0, 4.0), "xlarge": (4.0, 8.0)}

# 단계별 기본 구성. 예산을 모를 때 그대로 쓰고, 예산이 있으면 크기·최소 태스크 수만 따르고 max_tasks는 예산으로 정한다
# 값은 app-config.schema.json 의 task_size 규칙 안에 있어야 한다
TIERS = {
    "lean": {"label": "작게 시작", "task_size": "xsmall", "min_tasks": 1, "max_tasks": 1,
             "headline": "가장 저렴하게 시작", "tradeoff": "방문자가 몰리면 느려질 수 있고, 태스크 하나가 멈추면 잠시 끊김"},
    "balanced": {"label": "권장", "task_size": "small", "min_tasks": 1, "max_tasks": 2,
                 "headline": "일반적인 소규모 서비스에 맞춤", "tradeoff": "부하가 늘면 태스크를 최대 2개까지 자동으로 늘림"},
    "roomy": {"label": "여유 있게", "task_size": "medium", "min_tasks": 2, "max_tasks": 4,
              "headline": "항상 2개 이상을 띄워 끊김을 줄임", "tradeoff": "가장 비싸지만 한 태스크가 멈춰도 서비스가 유지됨"},
}
ORDER = ["lean", "balanced", "roomy"]
RANKS = {"lowest": "최저", "average": "평균", "highest": "최대"}

# 예상 사용자 수(프런트의 ExpectedUsers 값) → 권장 단계
USERS_TO_TIER = {"~100": "lean", "~1,000": "balanced", "~10,000": "roomy", "10,000+": "roomy"}


def load_prices(path=None):
    p = Path(path) if path else Path(__file__).with_name("prices.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _money(x):
    return round(float(x), 2)


def ipv4_count(prices, foundation=None):
    """공인 IPv4 주소 개수: ALB가 가용 영역마다 1개 + NAT 인스턴스의 탄력적 IP(대수만큼).

    배포된 foundation의 출력(deploy_inputs)을 주면 그 구성에서 센다. task_subnet_ids의 개수가 가용 영역 수이고,
    assign_public_ip가 true면 NAT 인스턴스가 없으며, nat_instance_count 출력이 있으면 그 값을 쓴다.
    foundation 정보가 없거나 NAT 대수를 알 수 없으면 prices.json의 foundation_assumptions(foundation 기본값과 같게 둔다)를 쓴다."""
    assume = prices.get("foundation_assumptions", {})
    azs, nats = assume.get("alb_azs", 3), assume.get("nat_eips", 3)
    if foundation:
        subnets = foundation.get("task_subnet_ids")
        if isinstance(subnets, list) and subnets:
            azs = len(subnets)
        if foundation.get("assign_public_ip") is True:
            nats = 0
        elif isinstance(foundation.get("nat_instance_count"), int):
            nats = foundation["nat_instance_count"]
    return azs + nats, azs, nats


def _task_cost(size, prices, arch, foundation):
    """태스크 1개의 월 비용을 (Fargate 컴퓨트, 태스크가 받는 공인 IPv4)로 나눠 돌려준다.

    NAT 인스턴스가 없는 구성(assign_public_ip=true)은 앱 태스크가 퍼블릭 서브넷에서 공인 IPv4를 직접 받는다.
    공인 IPv4는 사용 중인 주소마다 시간당 요금이 붙으므로 태스크마다 더한다(공용 비용이 아니라 태스크가 늘면 같이 늘어나는 앱 비용).
    foundation 정보를 모르면 기본 가정(NAT 있음)이라 더하지 않는다."""
    hours = prices.get("hours_per_month", HOURS_DEFAULT)
    vcpu, gb = SIZES[size]
    f = prices["fargate"][arch]
    compute = (vcpu * f["vcpu_hour"] + gb * f["gb_hour"]) * hours
    ipv4 = prices.get("public_ipv4_hour", 0.0) * hours if isinstance(foundation, dict) and foundation.get("assign_public_ip") is True else 0.0
    return compute, ipv4


def estimate(tier, prices, arch="X86_64", foundation=None, cfg=None):
    """한 단계의 월 비용. 앱 추가 비용(Fargate)과 공용 고정 비용(ALB, RDS, 스토리지, 공인 IPv4)을 나눠 담는다.

    total_monthly는 평소(min_tasks개)의 월 비용이고, peak_monthly는 부하가 최대(max_tasks개)일 때의 월 비용이다. 예산 판정은 peak_monthly로 한다.
    cfg를 주면 TIERS[tier] 대신 그 구성(task_size, min_tasks, max_tasks와 표시 문구)으로 계산한다.
    NAT 인스턴스·데이터 전송·로그 등은 prices.json 의 excluded 에 적힌 대로 제외한 값이다.
    """
    if tier not in TIERS:
        raise ValueError(f"알 수 없는 단계: {tier}")
    if arch not in prices["fargate"]:
        raise ValueError(f"알 수 없는 아키텍처: {arch}")
    cfg = TIERS[tier] if cfg is None else cfg
    if cfg["task_size"] not in SIZES:
        raise ValueError(f"알 수 없는 태스크 크기: {cfg['task_size']}")
    hours = prices.get("hours_per_month", HOURS_DEFAULT)
    vcpu, gb = SIZES[cfg["task_size"]]
    compute_one, ipv4_one = _task_cost(cfg["task_size"], prices, arch, foundation)
    tasks = cfg["min_tasks"]
    compute_monthly = compute_one * tasks
    task_ipv4_monthly = ipv4_one * tasks
    app_monthly = compute_monthly + task_ipv4_monthly
    app_peak_monthly = (compute_one + ipv4_one) * cfg["max_tasks"]
    alb = prices["alb_hour"] * hours
    rds = prices["rds"]
    rds_instance = rds["instance_hour"] * hours
    rds_storage = rds["storage_gb"] * rds["storage_gb_month"]
    # 공인 IPv4 주소는 사용 중이면 개당 시간당 요금이 붙는다(ALB 가용 영역마다 1개, NAT용 탄력적 IP)
    n_ipv4, n_azs, n_nats = ipv4_count(prices, foundation)
    ipv4 = prices.get("public_ipv4_hour", 0.0) * n_ipv4 * hours
    shared = alb + rds_instance + rds_storage + ipv4
    resources = [
        {"service": "ECS Fargate", "spec": f"{vcpu:g} vCPU / {gb:g} GB x {tasks}개 ({arch})", "monthlyUsd": _money(compute_monthly),
         "why": f"{cfg['label']} 단계. 평소 {tasks}개, 부하가 늘면 최대 {cfg['max_tasks']}개까지 늘어나며 늘어난 만큼 비용이 더해짐"},
    ]
    if task_ipv4_monthly:
        resources.append(
            {"service": "공인 IPv4 주소 (앱 태스크)", "spec": f"{tasks}개 (NAT 없음: 태스크가 공인 IP를 직접 받음)", "monthlyUsd": _money(task_ipv4_monthly),
             "why": "NAT 인스턴스가 없는 구성에서는 앱 태스크마다 공인 IPv4가 붙어 시간당 요금이 생김. 태스크가 늘면 같이 늘어남"})
    resources += [
        {"service": "Application Load Balancer", "spec": "1개 (공용)", "monthlyUsd": _money(alb),
         "why": "모든 앱이 함께 쓰는 공용 진입점. 앱이 늘어도 늘지 않음"},
        {"service": "RDS MySQL", "spec": f"{rds['instance_class']}, {rds['storage_gb']} GB (공용)", "monthlyUsd": _money(rds_instance + rds_storage),
         "why": "앱마다 전용 DB와 계정을 두지만 DB 서버는 공용"},
        {"service": "공인 IPv4 주소", "spec": f"{n_ipv4}개 (ALB 가용 영역 {n_azs}개 + NAT용 탄력적 IP {n_nats}개, 공용)", "monthlyUsd": _money(ipv4),
         "why": "AWS는 사용 중인 공인 IPv4 주소마다 시간당 요금을 받음"},
    ]
    return {
        "tier": tier, "label": cfg["label"], "task_size": cfg["task_size"], "min_tasks": cfg["min_tasks"], "max_tasks": cfg["max_tasks"],
        "headline": cfg["headline"], "tradeoff": cfg["tradeoff"],
        "app_monthly": _money(app_monthly), "shared_monthly": _money(shared), "total_monthly": _money(app_monthly + shared),
        "peak_monthly": _money(app_peak_monthly + shared),
        "resources": resources,
        "region": prices["region"], "currency": prices["currency"], "pricing_as_of": prices["pricing_as_of"],
        "excluded": list(prices.get("excluded", [])),
    }


def _tradeoff(tier, lo, hi):
    """예산으로 max_tasks를 정한 안의 장단점 문구. 프리셋 문구는 max_tasks가 박혀 있어 그대로 쓸 수 없다."""
    if tier == "balanced":
        return f"부하가 늘면 태스크를 최대 {hi}개까지 자동으로 늘림" if hi > lo else "부하가 늘어도 태스크를 늘리지 않음(예산 안에서 1개 고정)"
    if tier == "roomy":
        grow = f"부하가 늘면 최대 {hi}개까지 늘림" if hi > lo else "예산 안에서 그 수로 고정"
        return f"항상 {lo}개 이상을 띄우고 {grow}. 한 태스크가 멈춰도 서비스가 유지됨"
    return TIERS[tier]["tradeoff"]


def _fill_tier(tier, target, prices, arch, foundation):
    """프리셋 tier의 크기·최소 태스크 수는 그대로 두고, 부하가 최대일 때의 월 비용이 target 이하인 가장 큰 max_tasks를 정한다.
    최소 태스크 수도 담지 못하면 None."""
    base = TIERS[tier]
    shared = estimate(tier, prices, arch, foundation)["shared_monthly"]
    compute, ipv4 = _task_cost(base["task_size"], prices, arch, foundation)
    n = max(int((target - shared) // (compute + ipv4)), 0)

    def peak(k):
        return estimate(tier, prices, arch, foundation, {**base, "max_tasks": k})["peak_monthly"]

    # 공용 비용을 소수 둘째 자리로 반올림한 오차로 한 개 넘치거나 모자랄 수 있어 실제 금액으로 맞춘다.
    # 최소 태스크 수를 확인하기 전에 위아래로 모두 보정해야 한다(모자라게 나온 채로 거르면 딱 맞는 예산이 탈락한다)
    while n > 0 and peak(n) > target:
        n -= 1
    while peak(n + 1) <= target:
        n += 1
    if n < base["min_tasks"]:
        return None
    return {**base, "max_tasks": n, "tradeoff": _tradeoff(tier, base["min_tasks"], n)}


def budget_configs(budget_usd, prices, arch="X86_64", foundation=None):
    """월 예산을 넘지 않는 최저·평균·최대 세 안의 구성을 {tier: 구성}으로 돌려준다. 어느 안도 못 만들면 빈 딕셔너리.

    - 최저(lean): 가장 작은 구성(xsmall 1개 고정).
    - 최대(roomy): 부하가 최대일 때의 월 비용이 예산 한도에 닿도록 max_tasks를 채운 구성.
    - 평균(balanced): 최저와 최대의 중간 금액에 맞춰 max_tasks를 채운 구성.
    예산이 작아 최대(roomy)의 최소 구성(medium 2개)도 담지 못하면 balanced를 예산 한도까지 채워 최대 안으로 삼는다.
    그것도 못 담으면 최저 안만 남는다. 만들 수 없는 안은 뺀다.
    """
    budget = float(budget_usd)
    lean = estimate("lean", prices, arch, foundation)
    if lean["peak_monthly"] > budget:
        return {}
    cfgs = {"lean": dict(TIERS["lean"])}
    top = _fill_tier("roomy", budget, prices, arch, foundation)
    if top is not None:
        cfgs["roomy"] = top
        mid = _fill_tier("balanced", (lean["peak_monthly"] + budget) / 2, prices, arch, foundation)
    else:
        mid = _fill_tier("balanced", budget, prices, arch, foundation)
    if mid is not None:
        cfgs["balanced"] = mid
    return {t: cfgs[t] for t in ORDER if t in cfgs}


def option_summary(est, rank, recommended):
    """프런트·승인 화면에 보여줄 한 안의 요약. rank는 lowest/average/highest(최저·평균·최대 금액 순)."""
    return {"tier": est["tier"], "rank": rank, "label": est["label"], "task_size": est["task_size"],
            "min_tasks": est["min_tasks"], "max_tasks": est["max_tasks"],
            "total_monthly": est["total_monthly"], "peak_monthly": est["peak_monthly"],
            "headline": est["headline"], "tradeoff": est["tradeoff"], "recommended": recommended}


def _rank(i, n):
    return "lowest" if i == 0 else ("highest" if i == n - 1 else "average")


def recommend(expected_users, pattern, budget_usd, prices, arch="X86_64", foundation=None):
    """권장 단계를 고르고, 비교할 최저·평균·최대 안(options)을 함께 낸다.

    - 예산이 없으면 TIERS 프리셋 세 안을 쓴다. 예산이 있으면 budget_configs가 예산 안에서 세 안을 만든다.
    - 예상 사용자 수로 기본 단계를 정하고, 접속이 특정 시간에 몰리면(peak) 한 단계 올린다.
    - 예산 때문에 만들 수 없는 안이면 만들 수 있는 가장 큰 안으로 내린다. 예산 판정은 부하가 최대일 때의 월 비용(peak_monthly) 기준이다.
    - 가장 작은 안도 예산을 넘으면 recommended=None 과 이유를 낸다(AGENTS.md 5장 6단계: 만족할 구성이 없으면 이유를 알리고 종료).
    """
    budget = None if budget_usd is None else float(budget_usd)
    cfgs = {t: TIERS[t] for t in ORDER} if budget is None else budget_configs(budget, prices, arch, foundation)
    if not cfgs:
        estimates = {t: estimate(t, prices, arch, foundation) for t in ORDER}
        cheapest = estimates[ORDER[0]]["peak_monthly"]
        return {"recommended": None, "estimates": estimates, "options": [],
                "reason": f"월 예산 ${budget:.2f} 안에 들어가는 구성이 없습니다. 가장 작은 구성도 월 약 ${cheapest:.2f}입니다(공용 ALB·RDS 포함, 제외 항목 별도)."}
    estimates = {t: estimate(t, prices, arch, foundation, cfgs[t]) for t in cfgs}
    base = USERS_TO_TIER.get(expected_users, "balanced")
    idx = ORDER.index(base)
    reasons = []
    if expected_users in USERS_TO_TIER:
        reasons.append(f"예상 사용자 {expected_users}명에 맞는 {TIERS[base]['label']} 단계")
    else:
        reasons.append("예상 사용자 수를 몰라 권장 단계로 시작")
    if pattern == "peak" and idx < len(ORDER) - 1:
        idx += 1
        reasons.append("특정 시간에 접속이 몰려 한 단계 올림")
    if budget is not None:
        fitting = [i for i in range(idx + 1) if ORDER[i] in estimates]
        if fitting[-1] < idx:
            reasons.append(f"월 예산 ${budget:.2f}으로는 '{TIERS[ORDER[idx]]['label']}' 안을 만들 수 없어 낮춤")
        idx = fitting[-1]
        reasons.append(f"세 안 모두 부하가 최대일 때도 월 예산 ${budget:.2f}을 넘지 않음")
    tier = ORDER[idx]
    names = [t for t in ORDER if t in estimates]
    options = [option_summary(estimates[t], _rank(i, len(names)), t == tier) for i, t in enumerate(names)]
    return {"recommended": tier, "estimates": estimates, "options": options, "reason": ". ".join(reasons)}
