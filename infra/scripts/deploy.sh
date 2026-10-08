#!/usr/bin/env bash
# 배포 1건을 자동으로 만들고(up), 상태를 보고(status), 지운다(destroy).
# infra/README.md의 "배포 1건" 절차를 그대로 실행한다. 사람이 하던 명령을 순서대로 묶은 것이다.
#
# 사용법
#   deploy.sh up      --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --port 8001 --app app.json [--yes]
#   deploy.sh status  a1b2c3d4
#   deploy.sh destroy a1b2c3d4 [--yes]
#
# 필요한 것: terraform(>=1.6), aws CLI(자격증명 설정 완료). foundation은 먼저 apply되어 있어야 한다.
# 환경 변수: AWS_REGION(기본값은 foundation이 만들어진 리전. 없으면 aws configure의 리전)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/deployments/_template"
FOUNDATION="$ROOT/foundation"
FOUNDATION_JSON="$ROOT/deployments/foundation.json"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"

die() { echo "오류: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 이(가) 설치되어 있지 않습니다"; }

region() {
  local r="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  [ -n "$r" ] || r="$(aws configure get region 2>/dev/null || true)"
  [ -n "$r" ] || die "리전을 알 수 없습니다. AWS_REGION을 지정하세요"
  echo "$r"
}

deploy_dir() { echo "$ROOT/deployments/$1"; }

valid_id() {
  # 대상 그룹 이름 32자 제한 때문에 짧은 영숫자·하이픈만 받는다
  [[ "$1" =~ ^[a-z0-9][a-z0-9-]{1,15}$ ]] || die "deploy id는 소문자·숫자·하이픈 2~16자여야 합니다: $1"
}

confirm() {
  [ "${ASSUME_YES:-0}" = "1" ] && return 0
  read -r -p "$1 [yes/no] " ans
  [ "$ans" = "yes" ] || die "취소했습니다"
}

# foundation의 출력(deploy_inputs)을 배포가 읽을 JSON으로 저장한다
export_foundation() {
  terraform -chdir="$FOUNDATION" output -json deploy_inputs > "$FOUNDATION_JSON" \
    || die "foundation 출력을 읽지 못했습니다. foundation이 apply되었는지 확인하세요"
}

cmd_up() {
  local id="" image="" port="" app=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="$2"; shift 2 ;;
      --image) image="$2"; shift 2 ;;
      --port) port="$2"; shift 2 ;;
      --app) app="$2"; shift 2 ;;
      --arch) ARCH="$2"; shift 2 ;;
      --yes) ASSUME_YES=1; shift ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  [ -n "$id" ] && [ -n "$image" ] && [ -n "$port" ] && [ -n "$app" ] || die "--id --image --port --app 이 모두 필요합니다"
  valid_id "$id"
  [[ "$port" =~ ^[0-9]+$ ]] || die "--port는 숫자여야 합니다"
  [ -f "$app" ] || die "app 파일이 없습니다: $app"
  need terraform; need aws

  local d; d="$(deploy_dir "$id")"
  [ ! -e "$d" ] || die "이미 있는 배포입니다: $d (새 ID를 쓰거나 destroy 후 다시 하세요)"

  local rgn; rgn="$(region)"
  log "배포 $id 준비 (리전 $rgn)"

  export_foundation

  # provider를 배포마다 다시 받지 않도록 캐시를 쓴다
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  mkdir -p "$TF_PLUGIN_CACHE_DIR"

  mkdir -p "$d"
  tar -C "$TEMPLATE" --exclude=.terraform -cf - . | tar -C "$d" -xf -

  cat > "$d/platform.auto.tfvars.json" <<EOF
{"platform": {
  "region": "$rgn",
  "deploy_id": "$id",
  "image": "$image",
  "cpu_architecture": "${ARCH:-X86_64}",
  "listener_port": $port,
  "foundation": $(cat "$FOUNDATION_JSON")
}}
EOF
  # LLM 출력(승인된 값)은 app 변수로 감싸서 그대로 둔다
  printf '{"app": %s}\n' "$(cat "$app")" > "$d/app.auto.tfvars.json"

  log "init"
  terraform -chdir="$d" init -input=false >/dev/null
  log "plan"
  terraform -chdir="$d" plan -input=false -out=tfplan -no-color | tail -n 40
  terraform -chdir="$d" show -json tfplan > "$d/plan.json"
  log "승인 화면용 계획 저장: $d/plan.json"

  confirm "위 계획대로 apply 할까요?"
  log "apply"
  terraform -chdir="$d" apply -input=false -no-color tfplan | tail -n 15
  terraform -chdir="$d" output -json > "$d/outputs.json"

  wait_healthy "$id"
}

# 대상 그룹의 태스크가 healthy가 될 때까지 기다린다 (Terraform은 기다리지 않는다)
wait_healthy() {
  local id="$1" d tg start state
  d="$(deploy_dir "$id")"
  tg="$(terraform -chdir="$d" output -raw target_group_arn)"
  start=$(date +%s)
  log "헬스체크 대기 (최대 ${HEALTH_TIMEOUT}초)"
  while true; do
    state="$(aws elbv2 describe-target-health --region "$(region)" --target-group-arn "$tg" \
      --query 'TargetHealthDescriptions[].TargetHealth.State' --output text 2>/dev/null || true)"
    log "대상 상태: ${state:-등록 대기}"
    case "$state" in
      *healthy*) [[ "$state" != *unhealthy* ]] && { log "정상입니다: $(terraform -chdir="$d" output -raw url)"; return 0; } ;;
    esac
    if [ $(( $(date +%s) - start )) -ge "$HEALTH_TIMEOUT" ]; then
      log "시간 초과. 로그를 확인하세요:"
      echo "  aws logs tail $(terraform -chdir="$d" output -raw log_group_name) --since 10m --region $(region)"
      return 1
    fi
    sleep 10
  done
}

cmd_status() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: status <id>"
  need terraform; need aws
  local d; d="$(deploy_dir "$id")"; [ -d "$d" ] || die "없는 배포입니다: $id"
  local cluster svc
  cluster="$(terraform -chdir="$d" output -raw cluster_name)"
  svc="$(terraform -chdir="$d" output -raw service_name)"
  echo "URL: $(terraform -chdir="$d" output -raw url)"
  aws ecs describe-services --region "$(region)" --cluster "$cluster" --services "$svc" \
    --query 'services[0].{상태:status,원하는:desiredCount,실행중:runningCount,대기:pendingCount}' --output table
  aws elbv2 describe-target-health --region "$(region)" \
    --target-group-arn "$(terraform -chdir="$d" output -raw target_group_arn)" \
    --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason]' --output table
}

cmd_destroy() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: destroy <id> [--yes]"
  shift || true
  [ "${1:-}" = "--yes" ] && ASSUME_YES=1
  need terraform
  local d; d="$(deploy_dir "$id")"; [ -d "$d" ] || die "없는 배포입니다: $id"
  confirm "배포 $id 를 삭제할까요? (foundation은 지우지 않습니다)"
  terraform -chdir="$d" destroy -input=false -auto-approve -no-color | tail -n 10
  log "삭제했습니다. 폴더(state 포함)는 남겨 둡니다: $d"
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    up) cmd_up "$@" ;;
    status) cmd_status "$@" ;;
    destroy) cmd_destroy "$@" ;;
    *) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 1 ;;
  esac
}
main "$@"
