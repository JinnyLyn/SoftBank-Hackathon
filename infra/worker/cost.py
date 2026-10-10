"""예산·사용 규모에서 구성 단계(lean/balanced/roomy)와 월 비용을 계산한다. 표준 라이브러리만 쓴다.

비용은 LLM이 아니라 이 코드가 가격표(prices.json)로 계산한다(AGENTS.md 5장 6단계).
가격표의 리전·기준일·제외 항목을 결과에 함께 담아, 제외한 비용까지 포함한 총액처럼 보이지 않게 한다(AGENTS.md 6장).
task_size 프리셋(CPU·메모리)은 modules/ecs-web-app/main.tf의 표와 같아야 한다.
"""
import json
from pathlib import Path

HOURS_DEFAULT = 730

# modules/ecs-web-app/main.tf 의 size 프리셋과 같은 값(vCPU, GB)
SIZES = {"xsmall": (0.25, 0.5), "small": (0.5, 1.0), "medium": (1.0, 2.0)}

# 단계별 앱 구성. 값은 app-config.schema.json 의 task_size / min_tasks / max_tasks 규칙 안에 있어야 한다
TIERS = {
    "lean": {"label": "작게 시작", "task_size": "xsmall", "min_tasks": 1, "max_tasks": 1,
             "headline": "가장 저렴하게 시작", "tradeoff": "방문자가 몰리면 느려질 수 있고, 태스크 하나가 멈추면 잠시 끊김"},
    "balanced": {"label": "권장", "task_size": "small", "min_tasks": 1, "max_tasks": 2,
                 "headline": "일반적인 소규모 서비스에 맞춤", "tradeoff": "부하가 늘면 태스크를 최대 2개까지 자동으로 늘림"},
    "roomy": {"label": "여유 있게", "task_size": "medium", "min_tasks": 2, "max_tasks": 4,
              "headline": "항상 2개 이상을 띄워 끊김을 줄임", "tradeoff": "가장 비싸지만 한 태스크가 멈춰도 서비스가 유지됨"},
}
ORDER = ["lean", "balanced", "roomy"]

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


def estimate(tier, prices, arch="X86_64", foundation=None):
    """한 단계의 월 비용. 앱 추가 비용(Fargate)과 공용 고정 비용(ALB, RDS, 스토리지, 공인 IPv4)을 나눠 담는다.

    amount는 둘의 합이다. NAT 인스턴스·데이터 전송·로그 등은 prices.json 의 excluded 에 적힌 대로 제외한 값이다.
    """
    if tier not in TIERS:
        raise ValueError(f"알 수 없는 단계: {tier}")
    if arch not in prices["fargate"]:
        raise ValueError(f"알 수 없는 아키텍처: {arch}")
    hours = prices.get("hours_per_month", HOURS_DEFAULT)
    cfg = TIERS[tier]
    vcpu, gb = SIZES[cfg["task_size"]]
    f = prices["fargate"][arch]
    per_task = (vcpu * f["vcpu_hour"] + gb * f["gb_hour"]) * hours
    tasks = cfg["min_tasks"]
    compute_monthly = per_task * tasks
    # NAT 인스턴스가 없는 구성(assign_public_ip=true)은 앱 태스크가 퍼블릭 서브넷에서 공인 IPv4를 직접 받는다.
    # 공인 IPv4는 사용 중인 주소마다 시간당 요금이 붙으므로 태스크마다 더한다(공용 비용이 아니라 태스크가 늘면 같이 늘어나는 앱 비용).
    # foundation 정보를 모르면 기본 가정(NAT 있음)이라 더하지 않는다
    task_ipv4 = prices.get("public_ipv4_hour", 0.0) * hours if isinstance(foundation, dict) and foundation.get("assign_public_ip") is True else 0.0
    task_ipv4_monthly = task_ipv4 * tasks
    app_monthly = compute_monthly + task_ipv4_monthly
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
        "resources": resources,
        "region": prices["region"], "currency": prices["currency"], "pricing_as_of": prices["pricing_as_of"],
        "excluded": list(prices.get("excluded", [])),
    }


def recommend(expected_users, pattern, budget_usd, prices, arch="X86_64", foundation=None):
    """권장 단계를 고른다.

    - 예상 사용자 수로 기본 단계를 정하고, 접속이 특정 시간에 몰리면(peak) 한 단계 올린다.
    - 월 예산(합계 기준)을 넘는 단계는 고르지 않는다. 권장 단계가 예산을 넘으면 예산 안에서 가장 큰 단계로 내린다.
    - 가장 작은 단계도 예산을 넘으면 recommended=None 과 이유를 낸다(AGENTS.md 5장 6단계: 만족할 구성이 없으면 이유를 알리고 종료).
    """
    estimates = {t: estimate(t, prices, arch, foundation) for t in ORDER}
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
    if budget_usd is not None:
        fitting = [i for i in range(idx + 1) if estimates[ORDER[i]]["total_monthly"] <= float(budget_usd)]
        if not fitting:
            cheapest = estimates[ORDER[0]]["total_monthly"]
            return {"recommended": None, "estimates": estimates,
                    "reason": f"월 예산 ${float(budget_usd):.2f} 안에 들어가는 구성이 없습니다. 가장 작은 구성도 월 약 ${cheapest:.2f}입니다(공용 ALB·RDS 포함, 제외 항목 별도)."}
        if fitting[-1] < idx:
            reasons.append(f"월 예산 ${float(budget_usd):.2f}을 넘어 한 단계 낮춤")
        idx = fitting[-1]
    return {"recommended": ORDER[idx], "estimates": estimates, "reason": ". ".join(reasons)}
