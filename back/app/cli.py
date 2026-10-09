from __future__ import annotations

import argparse

from app.migrations import migrate


def main() -> None:
    parser = argparse.ArgumentParser(prog="paved-clouds-backend")
    parser.add_argument("command", choices=["migrate"])
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()


if __name__ == "__main__":
    main()
