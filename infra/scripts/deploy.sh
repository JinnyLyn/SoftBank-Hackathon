#!/usr/bin/env bash
# 배포 1건을 만들고(up), 이미지·설정을 바꾸고(update), 되돌리고(rollback), 상태를 보고(status),
# 실패 원인을 모으고(diagnose), 지운다(destroy).
# infra/README.md의 "배포 1건" 절차를 그대로 실행한다. 사람이 하던 명령을 순서대로 묶은 것이다.
#
# 사용법
#   deploy.sh up       --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --port 8001 --app app.json [--yes]
#   deploy.sh update   a1b2c3d4 [--image <ecr_url>:tag] [--app app.json] [--yes]
#   deploy.sh rollback a1b2c3d4 [--to <이미지>] [--yes]
#   deploy.sh apply    a1b2c3d4            # 위 명령을 --plan-only 로 만든, 승인된 저장 계획만 적용한다
#   deploy.sh status   a1b2c3d4
#   deploy.sh diagnose a1b2c3d4            # 실패 분석용 JSON(비밀 마스킹됨)을 표준 출력으로 낸다
#   deploy.sh destroy  a1b2c3d4 [--yes]
#
# 필요한 것: terraform(>=1.6), aws CLI(자격증명 설정 완료), python(diagnose의 JSON 조립. python3 또는 python).
# foundation은 먼저 apply되어 있어야 한다.
# 환경 변수: AWS_REGION(없으면 aws configure의 리전), HEALTH_TIMEOUT(초, 기본 300)
#
# 설계 규칙 (AGENTS.md 6장)
#   - apply는 항상 승인된 저장 plan(tfplan)만 실행한다. 승인 후 새 plan을 만들지 않는다.
#   - 롤백은 사람이 명령으로 실행한다. 자동 복구는 범위가 확정되기 전이라 만들지 않았다.
#   - 이력(history.log)에는 헬스체크를 통과한 이미지만 남긴다.
#   - 이미지를 되돌려도 DB 스키마와 데이터는 되돌아가지 않는다.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/deployments/_template"
FOUNDATION="$ROOT/foundation"
FOUNDATION_JSON="$ROOT/deployments/foundation.json"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"

die() { echo "오류: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

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

existing_dir() {
  local d; d="$(deploy_dir "$1")"
  [ -d "$d" ] || die "없는 배포입니다: $1"
  echo "$d"
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

tf() { local d="$1"; shift; terraform -chdir="$d" "$@"; }

# python3(Mac·Linux)와 python(Windows) 중 실제로 실행되는 것을 고른다. Windows의 python3는 Store 안내용 가짜 파일일 수 있다
pick_python() {
  local p
  for p in python3 python; do
    if "$p" -c 'import sys' >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  die "python을 찾지 못했습니다"
}

# --- 이력: 헬스체크를 통과한 이미지만 기록한다 -------------------------------------------
record_healthy() {
  local d="$1" image; image="$(tf "$d" output -raw image)"
  printf '%s|%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$image" >> "$d/history.log"
}

current_image() { tail -n 1 "$1/history.log" 2>/dev/null | cut -d'|' -f2; }

# 현재와 다른 가장 최근의 정상 이미지
previous_image() {
  local d="$1" cur; cur="$(current_image "$d")"
  cut -d'|' -f2 "$d/history.log" 2>/dev/null | tac | awk -v cur="$cur" '$0 != cur { print; exit }'
}

# plan 저장 → 승인 → 저장된 plan 그대로 apply → 헬스체크 대기
plan_confirm_apply() {
  local d="$1" id="$2"
  log "plan"
  tf "$d" plan -input=false -out=tfplan -no-color | tail -n 40
  tf "$d" show -json tfplan > "$d/plan.json"
  log "승인 화면용 계획 저장: $d/plan.json"

  if [ "${PLAN_ONLY:-0}" = "1" ]; then
    log "계획만 만들었습니다. 승인 후 적용: deploy.sh apply $id"
    return 0
  fi
  confirm "위 계획대로 apply 할까요?"
  apply_saved "$d" "$id"
}

# 저장된 plan(tfplan)만 적용한다. 새 plan을 만들지 않는다
apply_saved() {
  local d="$1" id="$2"
  [ -f "$d/tfplan" ] || die "저장된 계획이 없습니다. 먼저 --plan-only 로 계획을 만드세요"
  log "apply (저장된 계획)"
  tf "$d" apply -input=false -no-color tfplan | tail -n 15
  tf "$d" output -json > "$d/outputs.json"
  # 한 번 적용한 계획은 다시 쓸 수 없다. 남겨 두면 오해를 부르니 지운다
  rm -f "$d/tfplan"

  wait_healthy "$id" || { log "헬스체크 실패. 원인: deploy.sh diagnose $id"; return 1; }
  record_healthy "$d"
}

cmd_apply() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: apply <id>  (먼저 up/update/rollback --plan-only 로 계획을 만든다)"
  need terraform; need aws
  local d; d="$(existing_dir "$id")"
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  apply_saved "$d" "$id"
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
      --plan-only) PLAN_ONLY=1; shift ;;
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
  tf "$d" init -input=false >/dev/null
  plan_confirm_apply "$d" "$id"
}

# 이미지나 앱 설정을 바꿔 다시 배포한다. 롤백도 같은 경로를 쓴다
redeploy() {
  local id="$1" image="$2" app="$3" d
  d="$(existing_dir "$id")"
  need terraform; need aws
  if [ -n "$image" ]; then
    # 입력 파일의 image 값만 바꾼다. 태그는 덮어쓸 수 없으니 새 태그를 쓴다
    sed -i -E 's#("image": *")[^"]*(")#\1'"$image"'\2#' "$d/platform.auto.tfvars.json"
    grep -q "\"image\": \"$image\"" "$d/platform.auto.tfvars.json" || die "image 값을 바꾸지 못했습니다"
  fi
  if [ -n "$app" ]; then
    [ -f "$app" ] || die "app 파일이 없습니다: $app"
    printf '{"app": %s}\n' "$(cat "$app")" > "$d/app.auto.tfvars.json"
  fi
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  plan_confirm_apply "$d" "$id"
}

cmd_update() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: update <id> [--image 이미지] [--app 파일]"; shift
  local image="" app=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --image) image="$2"; shift 2 ;;
      --app) app="$2"; shift 2 ;;
      --yes) ASSUME_YES=1; shift ;;
      --plan-only) PLAN_ONLY=1; shift ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  [ -n "$image" ] || [ -n "$app" ] || die "--image 나 --app 중 하나는 필요합니다"
  redeploy "$id" "$image" "$app"
}

cmd_rollback() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: rollback <id> [--to 이미지]"; shift
  local to=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --to) to="$2"; shift 2 ;;
      --yes) ASSUME_YES=1; shift ;;
      --plan-only) PLAN_ONLY=1; shift ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  local d; d="$(existing_dir "$id")"
  [ -n "$to" ] || to="$(previous_image "$d")"
  [ -n "$to" ] || die "되돌릴 이전 정상 이미지가 이력에 없습니다 ($d/history.log)"
  log "롤백 대상 이미지: $to"
  log "주의: 이미지만 되돌립니다. DB 스키마와 데이터는 되돌아가지 않습니다"
  redeploy "$id" "$to" ""
}

# 대상 그룹의 태스크가 healthy가 될 때까지 기다린다 (Terraform은 기다리지 않는다)
wait_healthy() {
  local id="$1" d tg cluster svc start state stopped
  d="$(deploy_dir "$id")"
  tg="$(tf "$d" output -raw target_group_arn)"
  cluster="$(tf "$d" output -raw cluster_name)"
  svc="$(tf "$d" output -raw service_name)"
  start=$(date +%s)
  log "헬스체크 대기 (최대 ${HEALTH_TIMEOUT}초)"
  while true; do
    state="$(aws elbv2 describe-target-health --region "$(region)" --target-group-arn "$tg" \
      --query 'TargetHealthDescriptions[].TargetHealth.State' --output text 2>/dev/null || true)"
    # 롤링 교체 중에는 옛 태스크가 아직 healthy라서 대상 상태만 보면 일찍 통과한다.
    # ECS의 배포 상태가 COMPLETED(새 태스크가 모두 떠서 교체 끝)일 때만 통과한다
    rollout="$(aws ecs describe-services --region "$(region)" --cluster "$cluster" --services "$svc"       --query 'services[0].deployments[?status==`PRIMARY`]|[0].rolloutState' --output text 2>/dev/null || true)"
    log "대상=${state:-등록 대기} 배포=${rollout:-?}"
    if [ "$rollout" = "FAILED" ]; then
      log "ECS 배포가 실패했습니다"
      return 1
    fi
    if [ "$rollout" = "COMPLETED" ]; then
      case "$state" in
        *unhealthy*|*draining*|*initial*) ;;
        *healthy*) log "정상입니다: $(tf "$d" output -raw url)"; return 0 ;;
      esac
    fi
    # 태스크가 계속 뜨자마자 죽으면 시간 초과를 기다리지 않고 바로 실패로 본다
    stopped="$(aws ecs list-tasks --region "$(region)" --cluster "$cluster" --service-name "$svc" \
      --desired-status STOPPED --query 'length(taskArns)' --output text 2>/dev/null || echo 0)"
    if [ "${stopped:-0}" -ge 3 ]; then
      log "태스크가 반복해서 종료되었습니다(최근 ${stopped}개)"
      return 1
    fi
    if [ $(( $(date +%s) - start )) -ge "$HEALTH_TIMEOUT" ]; then
      log "시간 초과"
      return 1
    fi
    sleep 10
  done
}

cmd_status() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: status <id>"
  need terraform; need aws
  local d; d="$(existing_dir "$id")"
  local cluster svc
  cluster="$(tf "$d" output -raw cluster_name)"
  svc="$(tf "$d" output -raw service_name)"
  echo "URL: $(tf "$d" output -raw url)"
  echo "현재 이미지: $(tf "$d" output -raw image)"
  echo "정상 이력: $(wc -l < "$d/history.log" 2>/dev/null || echo 0)건"
  aws ecs describe-services --region "$(region)" --cluster "$cluster" --services "$svc" \
    --query 'services[0].{상태:status,원하는:desiredCount,실행중:runningCount,대기:pendingCount}' --output table
  aws elbv2 describe-target-health --region "$(region)" \
    --target-group-arn "$(tf "$d" output -raw target_group_arn)" \
    --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason]' --output table
}

# 비밀로 보이는 값을 가린다. 로그를 LLM에 넘기기 전에 반드시 거친다 (AGENTS.md 7장)
mask() {
  sed -E \
    -e 's#(mysql|postgres(ql)?|redis|mongodb)://[^:@/[:space:]]+:[^@/[:space:]]+@#\1://***:***@#g' \
    -e 's#AKIA[0-9A-Z]{16}#AKIA****************#g' \
    -e 's#(ASIA|AIDA)[0-9A-Z]{16}#\1****************#g' \
    -e 's#((password|passwd|pwd|secret|token|api[_-]?key)[=:" ]+)[^ ",;&]+#\1***#Ig'
}

# 실패 분석용 정보를 JSON으로 모은다: 서비스 이벤트, 중단된 태스크의 종료 사유, 대상 헬스, 최근 로그
cmd_diagnose() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: diagnose <id>"
  need terraform; need aws
  local PY; PY="$(pick_python)"
  local d rgn cluster svc tg lg; d="$(existing_dir "$id")"; rgn="$(region)"
  cluster="$(tf "$d" output -raw cluster_name)"; svc="$(tf "$d" output -raw service_name)"
  tg="$(tf "$d" output -raw target_group_arn)"; lg="$(tf "$d" output -raw log_group_name)"
  local tmp; tmp="$(mktemp -d)"

  aws ecs describe-services --region "$rgn" --cluster "$cluster" --services "$svc" \
    --query 'services[0].{status:status,desired:desiredCount,running:runningCount,pending:pendingCount,events:events[:8].message}' \
    --output json > "$tmp/service.json" 2>/dev/null || echo '{}' > "$tmp/service.json"

  local tasks; tasks="$(aws ecs list-tasks --region "$rgn" --cluster "$cluster" --service-name "$svc" --desired-status STOPPED \
    --query 'taskArns[:5]' --output text 2>/dev/null || true)"
  if [ -n "$tasks" ] && [ "$tasks" != "None" ]; then
    # shellcheck disable=SC2086
    aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks $tasks \
      --query 'tasks[].{stoppedAt:stoppedAt,stopCode:stopCode,stoppedReason:stoppedReason,containers:containers[].{name:name,exitCode:exitCode,reason:reason}}' \
      --output json > "$tmp/stopped.json" 2>/dev/null || echo '[]' > "$tmp/stopped.json"
  else
    echo '[]' > "$tmp/stopped.json"
  fi

  aws elbv2 describe-target-health --region "$rgn" --target-group-arn "$tg" \
    --query 'TargetHealthDescriptions[].{target:Target.Id,state:TargetHealth.State,reason:TargetHealth.Reason,description:TargetHealth.Description}' \
    --output json > "$tmp/targets.json" 2>/dev/null || echo '[]' > "$tmp/targets.json"

  MSYS_NO_PATHCONV=1 aws logs tail "$lg" --region "$rgn" --since 30m --format short 2>/dev/null | tail -n 60 | mask > "$tmp/logs.txt" || true

  ID="$id" IMAGE="$(tf "$d" output -raw image)" URL="$(tf "$d" output -raw url)" DIR="$tmp" HIST="$(tail -n 3 "$d/history.log" 2>/dev/null || true)" \
  "$PY" - <<'PY'
import json, os, re, sys
d = os.environ["DIR"]
def load(n):
    with open(os.path.join(d, n), encoding="utf-8") as f:
        return json.load(f)
def mask(s):
    s = re.sub(r"(mysql|postgres(?:ql)?|redis|mongodb)://[^:@/\s]+:[^@/\s]+@", r"\1://***:***@", s)
    return re.sub(r"AKIA[0-9A-Z]{16}", "AKIA****************", s)
with open(os.path.join(d, "logs.txt"), encoding="utf-8", errors="replace") as f:
    logs = f.read().splitlines()
out = {
    "deployId": os.environ["ID"],
    "image": os.environ["IMAGE"],
    "url": os.environ["URL"],
    "lastHealthyImages": [l.split("|")[1] for l in os.environ["HIST"].splitlines() if "|" in l],
    "service": load("service.json"),
    "stoppedTasks": load("stopped.json"),
    "targets": load("targets.json"),
    "logTail": logs,
}
sys.stdout.reconfigure(encoding="utf-8")
print(mask(json.dumps(out, ensure_ascii=False, indent=2)))
PY
  rm -rf "$tmp"
}

cmd_destroy() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: destroy <id> [--yes]"
  shift || true
  [ "${1:-}" = "--yes" ] && ASSUME_YES=1
  need terraform
  local d; d="$(existing_dir "$id")"
  confirm "배포 $id 를 삭제할까요? (foundation은 지우지 않습니다)"
  tf "$d" destroy -input=false -auto-approve -no-color | tail -n 10
  log "삭제했습니다. 폴더(state 포함)는 남겨 둡니다: $d"
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    up) cmd_up "$@" ;;
    update) cmd_update "$@" ;;
    rollback) cmd_rollback "$@" ;;
    apply) cmd_apply "$@" ;;
    status) cmd_status "$@" ;;
    diagnose) cmd_diagnose "$@" ;;
    destroy) cmd_destroy "$@" ;;
    *) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 1 ;;
  esac
}
main "$@"
