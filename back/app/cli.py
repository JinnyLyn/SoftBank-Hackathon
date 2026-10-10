from __future__ import annotations

import argparse

from app.artifact_migration import migrate_local_artifacts
from app.migrations import migrate


def main() -> None:
    parser = argparse.ArgumentParser(prog="paved-clouds-backend")
    parser.add_argument("command", choices=["migrate", "migrate-artifacts"])
    parser.add_argument(
        "--apply", action="store_true",
        help="migrate-artifacts 작업을 실제로 적용합니다. 생략하면 사전 점검만 합니다.",
    )
    args = parser.parse_args()
    if args.command == "migrate":
        if args.apply:
            parser.error("--apply는 migrate-artifacts 명령에서만 사용할 수 있습니다.")
        migrate()
    elif args.command == "migrate-artifacts":
        result = migrate_local_artifacts(apply=args.apply)
        print(result)


if __name__ == "__main__":
    main()
