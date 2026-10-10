"""AWS Price List API에서 Fargate 온디맨드 단가를 받아 prices.json과 비교한다.

기본은 비교만 하고, --write를 주면 prices.json의 fargate·pricing_as_of·source를 갱신한다.
인증이 필요 없는 공개 가격 파일(약 1MB)을 쓴다.

    python scripts/update_prices.py            # 비교만
    python scripts/update_prices.py --write    # 갱신

Python에 인증서 설정이 없어 내려받기가 실패하면, curl로 받은 파일을 --file로 넘긴다.
    curl -o ecs.json https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/ap-northeast-2/index.json
    python scripts/update_prices.py --file ecs.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

PRICES_PATH = Path(__file__).resolve().parents[1] / "paved_ai" / "prices.json"
URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/{region}/index.json"

# usagetype 접미사 → (아키텍처, 항목)
USAGE = {
    "Fargate-vCPU-Hours:perCPU": ("X86_64", "vcpu_hour"),
    "Fargate-GB-Hours": ("X86_64", "gb_hour"),
    "Fargate-ARM-vCPU-Hours:perCPU": ("ARM64", "vcpu_hour"),
    "Fargate-ARM-GB-Hours": ("ARM64", "gb_hour"),
}


def fetch(region: str, file: "Path | None" = None) -> tuple[dict, str]:
    if file:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(URL.format(region=region), timeout=60) as res:
            data = json.load(res)
    found: dict = {}
    for sku, product in data["products"].items():
        usage = product["attributes"].get("usagetype", "")
        key = next((v for k, v in USAGE.items() if usage.endswith("-" + k)), None)
        if not key:
            continue
        for term in data["terms"]["OnDemand"].get(sku, {}).values():
            for dim in term["priceDimensions"].values():
                arch, item = key
                # 0을 빼고 소수 그대로 (예: "0.0465600000" → "0.04656")
                found.setdefault(arch, {})[item] = dim["pricePerUnit"]["USD"].rstrip("0").rstrip(".")
    missing = [f"{a}.{i}" for a, i in USAGE.values() if i not in found.get(a, {})]
    if missing:
        raise SystemExit(f"가격 파일에서 찾지 못함: {', '.join(missing)}")
    return found, data["publicationDate"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="prices.json 갱신")
    parser.add_argument("--file", type=Path, help="내려받아 둔 가격 파일 (내려받기가 안 될 때)")
    args = parser.parse_args()

    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    fresh, published = fetch(prices["region"], args.file)

    changed = False
    for arch, items in fresh.items():
        for item, value in items.items():
            old = prices["fargate"].get(arch, {}).get(item)
            mark = "같음" if old == value else "변경"
            changed |= old != value
            print(f"{arch:7} {item:10} {old or '-':>10} → {value:>10}  {mark}")
    print(f"가격 파일 발행일: {published}")

    if not args.write:
        print("비교만 했습니다. 갱신하려면 --write")
        return
    prices["fargate"] = fresh
    prices["pricing_as_of"] = published[:10]
    prices["source"] = (
        f"AWS Price List API, AmazonECS {prices['region']} 온디맨드 (publicationDate {published}). "
        "scripts/update_prices.py로 갱신"
    )
    PRICES_PATH.write_text(json.dumps(prices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("prices.json 갱신 완료" if changed else "단가는 같고 기준일만 갱신")


if __name__ == "__main__":
    sys.exit(main())
