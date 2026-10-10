#!/usr/bin/env bash
# Linux/WSL + Docker Compose v2. 이 실행에서 만든 프로젝트만 정리한다.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."

command -v timeout >/dev/null
project="paved-back-ci-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
compose=(docker compose --env-file /dev/null --project-name "$project" -f scripts/ci/platform-stack.compose.yaml)
cleanup() {
  local result=$?
  trap - EXIT
  if (( result != 0 )); then
    echo '플랫폼 DB/API 검사 실패: 위 단계와 종료 코드를 확인하세요. 서비스 원문 로그는 출력하지 않습니다.' >&2
    timeout 20 "${compose[@]}" ps --all || true
  fi
  timeout 60 "${compose[@]}" down --volumes --remove-orphans --timeout 10 || result=1
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 로컬 서비스·비밀값을 상속하지 않는 별도 구성으로 이미지와 임시 DB를 준비한다.
echo '플랫폼 백엔드 Docker 빌드'
timeout 300 "${compose[@]}" build api
echo '격리 MySQL 8.4/API 기동'
timeout 180 "${compose[@]}" up --detach --wait --wait-timeout 150

# 실패 traceback에 DB 설정 등이 포함될 수 있으므로 원문 출력을 남기지 않는다.
for attempt in 1 2; do
  echo "마이그레이션 적용 검사 (${attempt}/2)"
  if ! timeout 60 "${compose[@]}" exec -T api python -m app.cli migrate >/dev/null 2>&1; then
    echo "마이그레이션 ${attempt}회차 실패 (원문 비공개)" >&2
    exit 1
  fi
done

echo '마이그레이션 이력 완전성 검사'
if ! timeout 30 "${compose[@]}" exec -T api python - >/dev/null 2>&1 <<'PY'
from app.database import connect
from app.migrations import MIGRATIONS_DIR

with connect() as connection, connection.cursor() as cursor:
    cursor.execute("SELECT version FROM schema_migrations")
    actual = {row["version"] for row in cursor.fetchall()}
expected = {path.name for path in MIGRATIONS_DIR.glob("*.sql")}
assert expected and actual == expected, "migration history mismatch"
PY
then
  echo '마이그레이션 이력 검사 실패 (원문 비공개)' >&2
  exit 1
fi
echo 'PASS: 모든 마이그레이션 이력이 두 차례 실행 후 확인됨'

echo '실제 DB/API 롤백 계약 검사 (AWS/LLM 실행 없음)'
timeout 180 "${compose[@]}" exec -T api python /ci/test_platform_back_rollbacks.py

echo '실제 DB/API 비밀 마스킹 계약 검사'
timeout 120 "${compose[@]}" exec -T api python /ci/test_platform_back_masking.py
