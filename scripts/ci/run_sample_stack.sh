#!/usr/bin/env bash
# sample-back이 합쳐진 뒤 실행한다. 이 실행에서 만든 Compose 프로젝트만 정리한다.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."

project="paved-ci-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
compose=(docker compose --project-name "$project" -f sample-back/docker-compose.yml)
cleanup() {
  local result=$?
  trap - EXIT
  if (( result != 0 )); then
    "${compose[@]}" ps --all || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 10 || result=1
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Compose의 healthy/completed_successfully 의존 순서를 따른다.
# init-db는 정상 종료하는 일회성 작업이므로 모든 서비스를 running으로 기다리지 않는다.
"${compose[@]}" up --build --detach
python3 - <<'PY'
import json
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
            if response.status == 200 and json.load(response) == {"status": "ok"}:
                break
    except (OSError, ValueError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit("실제 API/MySQL 헬스체크가 제한 시간 안에 준비되지 않았습니다.")
PY
python3 scripts/ci/test_sample_api.py --url http://127.0.0.1:8000
node scripts/ci/test_sample_front.mjs --url http://127.0.0.1:8000 --mode real

# 살아 있는 화면이나 MOCK이 DB 장애를 숨기는지 별도로 확인한다.
"${compose[@]}" stop db
python3 scripts/ci/test_sample_api.py --url http://127.0.0.1:8000 --expect-unhealthy
