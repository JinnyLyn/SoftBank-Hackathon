#!/usr/bin/env bash
# Linux/WSL foreground supervisor. 기존 서비스나 배포 앱은 종료하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'uv가 필요합니다. 설치 후 다시 실행하세요.' >&2; exit 2; }
exec uv run --no-project --no-config --python 3.12 --no-python-downloads python scripts/onprem_service.py "$@"
