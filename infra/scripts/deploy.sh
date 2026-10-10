#!/usr/bin/env bash
# 배포 1건을 만들고(up), 이미지·설정을 바꾸고(update), 되돌리고(rollback), 상태를 보고(status),
# 실패 원인을 모으고(diagnose), 지운다(destroy).
# infra/README.md의 "배포 1건" 절차를 그대로 실행한다. 사람이 하던 명령을 순서대로 묶은 것이다.
#
# 사용법
#   deploy.sh build    --id a1b2c3d4 --source <ZIP|폴더> [--dockerfile 경로] [--tag 태그] [--arch X86_64|ARM64]   # 이미지를 빌드해 ECR에 올리고 주소를 출력
#   deploy.sh make-id  "프로젝트 이름"      # 이름(한글 포함)에서 배포 ID를 만든다
#   deploy.sh image-ref a1b2c3d4           # 이 배포의 이미지 주소(ECR 주소:태그)를 미리 정해 출력한다(plan용, build --tag 와 짝)
#   deploy.sh detect-arch                  # 이 PC의 docker 기준 X86_64 또는 ARM64를 출력한다
#   deploy.sh up       (--id a1b2c3d4 | --name "프로젝트 이름") --image <ecr_url>:a1b2c3d4-r1 --app app.json [--port 8001|auto] [--yes]
#                      [--plan-only] [--shared-db] [--grace 초] [--skip-nat-check] [--arch ARM64]   # 아키텍처 기본값은 이 PC의 docker
#   deploy.sh update   a1b2c3d4 [--image <ecr_url>:tag] [--app app.json] [--yes] [--plan-only]
#   deploy.sh rollback a1b2c3d4 --plan-only [--to <이미지>]   # 저수준 계획 생성. 제품 롤백은 아래 승인·연동 조건 필요
#   deploy.sh apply    a1b2c3d4            # 위 명령을 --plan-only 로 만든, 승인된 저장 계획만 적용한다
#   deploy.sh status   a1b2c3d4
#   deploy.sh diagnose a1b2c3d4            # 실패 분석용 JSON(비밀 마스킹됨)을 표준 출력으로 낸다
#   deploy.sh db-check a1b2c3d4            # 앱 전용 DB 계정이 자기 DB만 쓸 수 있는지 확인한다
#   deploy.sh destroy  a1b2c3d4 [--yes] [--drop-db]
#   deploy.sh drop-db  a1b2c3d4 [--yes]    # 앱 전용 DB와 계정을 지운다 (되돌릴 수 없다)
#   deploy.sh foundation-info              # foundation의 최신 출력을 JSON 한 줄로 낸다(읽기 전용. worker가 비용 계산 전에 호출)
#   deploy.sh foundation-state             # foundation state를 S3로 옮긴다 (PAVED_STATE_BUCKET 필요)
#
# 필요한 것: terraform(>=1.6, S3 state(PAVED_STATE_BUCKET)를 쓰면 >=1.10), aws CLI(자격증명 설정 완료), python(python3 또는 python),
#            앱별 DB를 쓰려면 docker(mysql 클라이언트 이미지를 ECR에 올릴 때 최초 1회).
# foundation은 먼저 apply되어 있어야 한다.
# 환경 변수
#   AWS_REGION          리전. 없으면 aws configure의 리전
#   HEALTH_TIMEOUT      헬스체크 대기 시간(초, 기본 300)
#   PAVED_STATE_BUCKET  지정하면 배포 state를 이 S3 버킷에 저장한다(infra/bootstrap으로 만든다). 없으면 로컬 파일
#
# 설계 규칙 (AGENTS.md 5·6장, infra/README.md의 "롤백")
#   - apply는 항상 승인된 저장 plan(tfplan)만 실행한다. 승인 후 새 plan을 만들지 않는다.
#   - 자동 롤백은 하지 않는다(확정 정책). 첫 실패는 진단·수정안 → 새 계획·사용자 승인 → 재배포다.
#   - 이후 실패의 제품 롤백은 백엔드 후보 선택 → 새 저장 plan·SHA-256·diff 등록 → 사용자 승인 → 별도 실행 순서다.
#     이 CLI의 대상 선택·확인 프롬프트는 제품 승인을 대신하지 않는다. --plan-only 없는 rollback은 apply까지 진행할 수 있다.
#     worker의 롤백 연동은 아직 미구현이므로 사람이 rollback/apply를 직접 실행해 공백을 메우지 않는다.
#   - 이력(history.log)에는 헬스체크를 통과한 배포만 남기고(롤백 대상), 모든 시도는 attempts.log에 성공·실패를 남긴다.
#   - 롤백은 이미지와 그때의 앱 설정을 함께 되돌린다(스냅샷).
#   - 이미지를 되돌려도 DB 스키마와 데이터는 되돌아가지 않는다.
#   - 앱 전용 DB는 destroy로 지우지 않는다(데이터 보존). 지우려면 --drop-db 또는 drop-db를 명시한다.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/deployments/_template"
FOUNDATION="$ROOT/foundation"
FOUNDATION_JSON="$ROOT/deployments/foundation.json"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"
TOOLS_TAG="tools-mysql84"

die() { echo "오류: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 이(가) 설치되어 있지 않습니다"; }

# Windows(Git Bash)에서 /로 시작하는 인자(SSM 이름, 로그 그룹)를 경로로 바꾸지 않게 하는 aws 래퍼.
# 전역으로 끄면 terraform.exe에 넘기는 경로 변환까지 꺼지므로 aws 호출에만 쓴다
awsn() { MSYS_NO_PATHCONV=1 aws "$@"; }

# 네이티브 프로그램(docker 등)에 넘길 경로. Git Bash에서는 Windows 경로로 바꾼다
native_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi; }

# aws CLI의 file:// 인자에 넣을 경로. Git Bash에서는 C:/Users/... 형태(슬래시)여야 aws.exe가 읽는다(실제 AWS로 확인)
file_uri_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }

region() {
  local r="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  [ -n "$r" ] || r="$(aws configure get region 2>/dev/null || true)"
  [ -n "$r" ] || die "리전을 알 수 없습니다. AWS_REGION을 지정하세요"
  echo "$r"
}

deploy_dir() { echo "$ROOT/deployments/$1"; }

valid_id() {
  # 모듈(variables.tf)의 deploy_id 규칙과 같아야 한다. 대상 그룹 이름 32자 제한 때문에 짧게 받는다
  [[ "$1" =~ ^[a-z0-9]{4,8}$ ]] || die "deploy id는 소문자·숫자 4~8자여야 합니다 (하이픈 불가): $1"
}

# 배포 ID로 폴더를 찾는다. ID 형식을 먼저 검사한다: 검사하지 않으면 `destroy ../foundation` 같은 값이
# deployments 밖의 Terraform 폴더(foundation)를 가리켜 공유 인프라를 지울 수 있다
existing_dir() {
  valid_id "$1"
  local d; d="$(deploy_dir "$1")"
  [ -d "$d" ] || die "없는 배포입니다: $1"
  echo "$d"
}

# up이 쓸 폴더를 확보한다. 이미 있으면 거부하되, destroy가 끝난 폴더(destroyed 표식)는 보관 폴더로 옮기고 같은 ID를 다시 쓸 수 있게 한다
claim_deploy_dir() {
  local id="$1" d; d="$(deploy_dir "$id")"
  [ -e "$d" ] || return 0
  if [ -f "$d/destroyed" ]; then
    local arch="$ROOT/deployments/_destroyed"
    mkdir -p "$arch"
    mv "$d" "$arch/$id-$(date -u +%Y%m%dT%H%M%SZ)"
    log "이전에 삭제한 같은 ID의 폴더를 deployments/_destroyed로 옮겼습니다"
    return 0
  fi
  die "이미 있는 배포입니다: $d (삭제하려면 destroy를 먼저 실행하세요. 계획만 만들고 멈춘 배포라면 apply 하세요)"
}

# up이 apply를 시작하기 전에 실패하거나 취소되면 반쯤 만들어진 폴더를 지운다(계속 남으면 같은 ID로 다시 만들 수 없다).
# apply가 시작된 뒤에는 state가 생겼을 수 있으므로 절대 지우지 않는다
cleanup_failed_up() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "${UP_PHASE:-}" = "prep" ] && [ -n "${UP_DIR:-}" ] && [ -d "$UP_DIR" ]; then
    rm -rf "${UP_DIR:?}"
    echo "[정리] 만들다 만 폴더를 지웠습니다: $UP_DIR" >&2
  fi
  return $rc
}

confirm() {
  [ "${ASSUME_YES:-0}" = "1" ] && return 0
  read -r -p "$1 [yes/no] " ans
  [ "$ans" = "yes" ] || die "취소했습니다"
}

# python3(Mac·Linux)와 python(Windows) 중 실제로 실행되는 것을 고른다. Windows의 python3는 Store 안내용 가짜 파일일 수 있다
pick_python() {
  local p
  for p in python3 python; do
    if "$p" -c 'import sys' >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  die "python을 찾지 못했습니다"
}

# JSON 파일에서 값 하나를 꺼낸다. 사용: jget 파일 키 [하위키...]  (리스트는 JSON으로, 문자열은 그대로 출력)
jget() {
  local PY; PY="$(pick_python)"
  "$PY" - "$@" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for k in sys.argv[2:]:
    d = d[k]
sys.stdout.reconfigure(encoding="utf-8")
print(d if isinstance(d, str) else json.dumps(d))
PYEOF
}

fjson() { jget "$FOUNDATION_JSON" "$@"; }

tf() { local d="$1"; shift; terraform -chdir="$d" "$@"; }

# foundation의 출력(deploy_inputs)을 배포가 읽을 JSON으로 저장한다
export_foundation() {
  terraform -chdir="$FOUNDATION" output -json deploy_inputs > "$FOUNDATION_JSON" \
    || die "foundation 출력을 읽지 못했습니다. foundation이 apply되었는지 확인하세요"
  # 옛 foundation(앱 공용 보안 그룹 구조)이면 새 모듈과 맞지 않는다
  fjson alb_security_group_id >/dev/null 2>&1 \
    || die "foundation이 옛 버전입니다(alb_security_group_id 없음). foundation을 새 코드로 다시 apply하세요"
}

# --- NAT 점검 ------------------------------------------------------------------------------------
# NAT를 쓰는 foundation이면 NAT 인스턴스가 실제로 초기화됐는지 부팅 로그로 확인한다.
# 초기화가 실패해도 인스턴스 상태 검사는 정상으로 나오므로, 이 확인이 없으면 NAT가 죽은 채로 배포가 시작되고
# 태스크가 ECR 접속 시간 초과로 실패한다 (t4g.nano에서 iptables-services 설치가 메모리 부족으로 종료된 사례).
# 부팅 직후라 로그가 아직 없을 수 있어 NAT_CHECK_WAIT초(기본 90)까지 기다린다.
# 그래도 확인하지 못하면 실패로 본다. 오래 실행된 인스턴스는 부팅 로그가 밀려났을 수 있어 경고만 한다.
# 건너뛰려면 --skip-nat-check
nat_check_one() {
  local id="$1" rgn="$2" out
  out="$(aws ec2 get-console-output --region "$rgn" --instance-id "$id" --latest --output text 2>/dev/null || true)"
  if printf '%s' "$out" | grep -Eqi 'oom-kill|Out of memory|Killed +dnf'; then echo FAIL
  elif printf '%s' "$out" | grep -Eq 'Created symlink.*iptables\.service'; then echo OK
  else echo UNKNOWN; fi
}

nat_preflight() {
  [ "${SKIP_NAT_CHECK:-0}" = "1" ] && { log "NAT 점검을 건너뜁니다"; return 0; }
  grep -Eq '"assign_public_ip": *false' "$FOUNDATION_JSON" || return 0   # NAT 없이 퍼블릭 서브넷에서 실행하는 구성
  local rgn vpc ids id res waited wait_max="${NAT_CHECK_WAIT:-90}" launched age
  rgn="$(region)"
  vpc="$(fjson vpc_id)"
  ids="$(aws ec2 describe-instances --region "$rgn" \
    --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=*-nat-*" "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)"
  [ -n "$ids" ] || die "NAT 인스턴스를 찾지 못했습니다. foundation이 enable_nat_instance=true로 만들어졌는지 확인하세요"
  for id in $ids; do
    waited=0
    while true; do
      res="$(nat_check_one "$id" "$rgn")"
      [ "$res" != "UNKNOWN" ] && break
      [ "$waited" -ge "$wait_max" ] && break
      sleep 10; waited=$((waited + 10))
    done
    case "$res" in
      OK) log "NAT $id 초기화 확인" ;;
      FAIL) die "NAT $id 의 초기화가 메모리 부족으로 실패했습니다. nat_instance_type을 small 이상으로 하고 인스턴스를 교체하세요" ;;
      *)
        # 부팅한 지 1시간이 지났으면 로그가 밀려났을 수 있다. 그 외에는 확인 실패로 본다
        launched="$(aws ec2 describe-instances --region "$rgn" --instance-ids "$id" --query 'Reservations[0].Instances[0].LaunchTime' --output text 2>/dev/null || true)"
        age="$("$(pick_python)" -c "import sys,datetime as d;t=sys.argv[1];print(int((d.datetime.now(d.timezone.utc)-d.datetime.fromisoformat(t.replace('Z','+00:00'))).total_seconds()))" "$launched" 2>/dev/null || echo 0)"
        if [ "${age:-0}" -gt 3600 ]; then
          log "경고: NAT $id 는 ${age}초 전에 부팅해서 부팅 로그에서 초기화를 확인하지 못했습니다. 외부 통신이 되는지 첫 배포의 헬스체크로 확인하세요"
        else
          die "NAT $id 의 초기화를 ${wait_max}초 안에 확인하지 못했습니다. 부팅 로그를 확인하거나 --skip-nat-check 로 건너뛰세요"
        fi
        ;;
    esac
  done
}

# --- 포트 자동 할당 ------------------------------------------------------------------------------
# 공유 ALB의 리스너 포트는 두 곳에서 쓰이는 것으로 본다.
#   (1) AWS에 이미 적용된 리스너
#   (2) 이 PC에서 계획만 만들고 아직 apply하지 않은 배포(승인 대기 계획 포함)
# (2)를 빼면 승인 전에 순서대로 계획한 두 프로젝트가 같은 포트(8001)를 골라서, 첫 계획을 적용한 뒤 두 번째 계획이
# 같은 ALB 포트에 다른 대상 그룹의 리스너를 만들다 DuplicateListener로 실패한다.
# (2)는 배포 폴더가 있는 동안 예약이다. 폴더가 지워지거나(계획 폐기, 만들다 만 폴더 정리) destroy 표식이 생기면 풀린다.
# 승인된 계획의 포트는 계획에 고정돼 있어서, 바꾸려면 새 계획과 새 승인이 필요하다.
local_reserved_ports() {  # local_reserved_ports <제외할 배포 ID>
  "$(pick_python)" - "$ROOT/deployments" "${1:-}" <<'PYEOF'
import json, os, sys
root, skip = sys.argv[1], sys.argv[2]
ports = set()
try:
    names = os.listdir(root)
except OSError:
    names = []
for n in names:
    d = os.path.join(root, n)
    # _template, _destroyed 같은 보관 폴더와 숨김 폴더(.port.lock)는 배포가 아니다. 삭제된 배포(destroyed 표식)는 포트를 놓는다
    if n == skip or n.startswith(("_", ".")) or not os.path.isdir(d) or os.path.exists(os.path.join(d, "destroyed")):
        continue
    try:
        with open(os.path.join(d, "platform.auto.tfvars.json"), encoding="utf-8") as f:
            p = json.load(f)["platform"]["listener_port"]
        if isinstance(p, int) and not isinstance(p, bool):
            ports.add(p)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:   # 포트를 고른 직후(입력 파일을 쓰기 전)의 예약
        with open(os.path.join(d, "port.reserved"), encoding="utf-8") as f:
            ports.add(int(f.read().strip()))
    except (OSError, ValueError):
        pass
print(*sorted(ports))
PYEOF
}

# mkdir은 원자적이라 잠금으로 쓴다. 두 up이 동시에 같은 포트를 고르지 않게 "고르기 + 예약 기록"을 한 번에 한다.
# 기다리는 시간(초)은 PORT_LOCK_WAIT(기본 30). 이 잠금이 남아 있으면(up이 강제 종료됨) deployments/.port.lock 폴더를 지운다
port_lock() {
  local lock="$ROOT/deployments/.port.lock" i=0
  mkdir -p "$ROOT/deployments"
  until mkdir "$lock" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -le "${PORT_LOCK_WAIT:-30}" ] || die "포트 예약 잠금을 얻지 못했습니다: $lock (다른 up이 끝나길 기다리거나, 강제 종료로 남은 잠금이면 폴더를 지우세요)"
    sleep 1
  done
}
port_unlock() { rmdir "$ROOT/deployments/.port.lock" 2>/dev/null || true; }

pick_port() {  # pick_port [제외할 배포 ID]: 허용 범위에서 AWS 리스너와 로컬 예약 포트를 뺀 가장 작은 빈 포트
  local rgn alb from to used reserved PY
  rgn="$(region)"; alb="$(fjson alb_arn)"; PY="$(pick_python)"
  from="$(fjson allowed_listener_ports from)"; to="$(fjson allowed_listener_ports to)"
  # 조회가 실패하면 중단한다. 빈 값으로 넘어가면 이미 쓰는 8001을 다시 골라 apply에서야 포트 중복으로 실패한다
  used="$(aws elbv2 describe-listeners --region "$rgn" --load-balancer-arn "$alb" --query 'Listeners[].Port' --output text)" \
    || die "ALB 리스너 목록을 조회하지 못했습니다. 자격증명과 권한을 확인하거나 --port 로 포트를 직접 지정하세요"
  reserved="$(local_reserved_ports "${1:-}")" || die "배포 폴더의 포트 예약을 읽지 못했습니다"
  "$PY" - "$from" "$to" "$used $reserved" <<'PYEOF'
import sys
lo, hi = int(sys.argv[1]), int(sys.argv[2])
used = {int(x) for x in sys.argv[3].split()}
for p in range(lo, hi + 1):
    if p not in used:
        print(p); break
else:
    sys.exit(1)
PYEOF
}

# 빈 포트를 고르고 곧바로 이 배포 폴더에 예약으로 기록한다(잠금 안에서). 포트를 출력한다
reserve_port() {  # reserve_port <배포 ID> <배포 폴더>
  local id="$1" d="$2" p
  port_lock
  if ! p="$(pick_port "$id")"; then port_unlock; return 1; fi
  printf '%s\n' "$p" > "$d/port.reserved"
  port_unlock
  echo "$p"
}

# --- state 위치 ----------------------------------------------------------------------------------
# PAVED_STATE_BUCKET이 있으면 배포 state를 S3에 둔다. 로컬 파일은 잃어버리면 AWS 리소스를 지울 수 없고,
# DB 비밀번호가 평문으로 들어 있다. S3는 버전 관리·암호화·잠금(use_lockfile)을 쓴다.
# 템플릿의 backend "local"을 override 파일로 바꾼다.
state_setup() {
  local d="$1" id="$2"
  [ -n "${PAVED_STATE_BUCKET:-}" ] || return 0
  require_tf_for_s3
  write_backend "$d" "deployments/${id}/terraform.tfstate"
  log "state를 S3에 저장합니다: s3://${PAVED_STATE_BUCKET}/deployments/${id}/terraform.tfstate"
}

# S3 backend 설정 파일을 만든다(배포와 foundation이 같이 쓴다). 사용: write_backend <폴더> <state 키>
write_backend() {
  cat > "$1/backend_override.tf" <<EOF
terraform {
  backend "s3" {
    bucket       = "${PAVED_STATE_BUCKET}"
    key          = "$2"
    region       = "${PAVED_STATE_REGION:-$(region)}"
    encrypt      = true
    use_lockfile = true
  }
}
EOF
}

# S3 잠금(use_lockfile)은 Terraform 1.10부터 지원한다. 낮은 버전은 init에서 알 수 없는 인수라는 오류로 멈추므로 먼저 알려 준다
tf_version() {
  "$(pick_python)" -c "import json,subprocess,sys;print(json.loads(subprocess.run(['terraform','version','-json'],capture_output=True,text=True).stdout)['terraform_version'])" 2>/dev/null || true
}

require_tf_for_s3() {
  local v; v="$(tf_version)"
  [ -n "$v" ] || die "terraform 버전을 확인하지 못했습니다"
  "$(pick_python)" -c "import sys;t=tuple(int(x) for x in sys.argv[1].split('-')[0].split('.')[:2]);sys.exit(0 if t>=(1,10) else 1)" "$v" \
    || die "S3 state(PAVED_STATE_BUCKET)는 Terraform 1.10 이상이 필요합니다(현재 $v)"
}

# foundation의 state도 S3로 옮긴다. foundation은 DB 비밀번호가 든 state를 가장 많이 담고 있어서 우선순위가 높다.
# 로컬 state가 있으면 그대로 복사하고, 없으면 새로 시작한다. 환경마다 값이 다른 backend_override.tf는 Git에서 제외된다.
# S3에 이미 foundation state가 있으면 로컬 state로 덮어쓰지 않는다(다른 PC가 올린 최신 state를 잃지 않도록)
cmd_foundation_state() {
  [ -n "${PAVED_STATE_BUCKET:-}" ] || die "PAVED_STATE_BUCKET을 지정하세요 (infra/bootstrap으로 만든 버킷)"
  need terraform; need aws
  require_tf_for_s3
  local key="foundation/terraform.tfstate" out remote=0 localstate="$FOUNDATION/terraform.tfstate"
  if out="$(awsn s3api head-object --bucket "$PAVED_STATE_BUCKET" --key "$key" --region "${PAVED_STATE_REGION:-$(region)}" 2>&1)"; then
    remote=1
  elif ! printf '%s' "$out" | grep -Eqi 'Not Found|404|NoSuchKey'; then
    die "S3에서 foundation state를 확인하지 못했습니다: $(printf '%s' "$out" | head -c 200)"
  fi
  write_backend "$FOUNDATION" "$key"
  if [ "$remote" = "1" ]; then
    if [ -s "$localstate" ]; then
      rm -f "$FOUNDATION/backend_override.tf"
      die "S3에 이미 foundation state가 있고 이 PC에도 로컬 state가 있습니다. 덮어쓰면 최신 state를 잃을 수 있어 중단합니다. 로컬 terraform.tfstate가 낡은 것이 맞다면 지우고 다시 실행하세요"
    fi
    tf "$FOUNDATION" init -input=false -reconfigure >/dev/null
    log "S3의 foundation state를 이 PC에서 쓰도록 연결했습니다: s3://${PAVED_STATE_BUCKET}/$key"
    return 0
  fi
  tf "$FOUNDATION" init -input=false -migrate-state -force-copy >/dev/null
  log "foundation state를 S3로 옮겼습니다: s3://${PAVED_STATE_BUCKET}/$key"
  log "로컬 terraform.tfstate 파일은 더 이상 쓰이지 않습니다. 확인한 뒤 지우세요"
}

# --- 앱별 DB와 계정 ------------------------------------------------------------------------------
# 모든 앱이 DB 관리자 계정으로 같은 DB를 쓰면 앱 하나가 뚫렸을 때 다른 앱의 데이터까지 읽고 지울 수 있다.
# 그래서 배포마다 전용 DB(app_<id>)와 그 DB에만 권한이 있는 계정을 만든다.
# DB는 프라이빗 서브넷에 있어서 VPC 안에서 mysql 클라이언트를 한 번 실행하는 Fargate 작업으로 만든다.
db_name_of() { echo "app_$1"; }

# 앱 전용 DB 접속 정보 파라미터의 이름과 ARN. 이름 규칙은 foundation의 IAM 정책(.../apps/*)과 맞아야 한다
db_param_name() {
  local base; base="$(fjson database_url_parameter_arn)"
  base="${base#*:parameter}"     # /<프로젝트>/database-url
  echo "${base%/database-url}/apps/$1/database-url"
}

db_param_arn() {
  local arn; arn="$(fjson database_url_parameter_arn)"
  echo "${arn%%:parameter*}:parameter$(db_param_name "$1")"
}

# mysql 클라이언트 이미지를 foundation ECR에 한 번만 올린다
ensure_tools_image() {
  local rgn ecr repo registry cfg
  rgn="$(region)"; ecr="$(fjson ecr_repository_url)"; repo="${ecr#*/}"; registry="${ecr%%/*}"
  if aws ecr describe-images --region "$rgn" --repository-name "$repo" --image-ids "imageTag=$TOOLS_TAG" >/dev/null 2>&1; then
    return 0
  fi
  need docker
  log "mysql 클라이언트 이미지를 ECR에 올립니다 (최초 1회)"
  cfg="$(mktemp -d)"; echo '{}' > "$cfg/config.json"
  # 사용자의 docker 설정(자격증명 도우미)을 건드리지 않으려고 임시 설정 폴더를 쓴다
  # ( ... ) || 안에서는 set -e가 꺼지므로 단계마다 직접 검사한다
  (
    set -o pipefail
    export DOCKER_CONFIG; DOCKER_CONFIG="$(native_path "$cfg")"
    aws ecr get-login-password --region "$rgn" | docker login --username AWS --password-stdin "$registry" >/dev/null || exit 1
    docker pull --platform linux/amd64 public.ecr.aws/docker/library/mysql:8.4 >/dev/null || exit 1
    docker tag public.ecr.aws/docker/library/mysql:8.4 "$ecr:$TOOLS_TAG" || exit 1
    docker push "$ecr:$TOOLS_TAG" >/dev/null || exit 1
  ) || { rm -rf "${cfg:?}"; die "mysql 클라이언트 이미지를 올리지 못했습니다"; }
  rm -rf "${cfg:?}"
}

# 1회성 DB 작업의 작업 정의 JSON을 만든다. mode: provision | drop | verify
db_taskdef_json() {
  local mode="$1" id="$2" PY; PY="$(pick_python)"
  # 값은 먼저 모두 읽어 둔다. 같은 줄에서 MSYS_NO_PATHCONV=1을 앞에 두면 뒤쪽 $(fjson ...)에도 적용되어
  # Python이 /c/... 경로를 열지 못한다
  local family="paved-clouds-dbinit-$id-$mode" rgn ecr exec_role lg host port admin_user admin_arn app_arn app_db other_db
  rgn="$(region)"; ecr="$(fjson ecr_repository_url)"
  # 관리자 비밀번호는 앱이 쓰는 공유 실행 역할이 읽을 수 없다. DB 작업은 전용 실행 역할을 쓴다
  exec_role="$(fjson db_provisioner_execution_role_arn 2>/dev/null)" \
    || die "foundation이 옛 버전입니다(db_provisioner_execution_role_arn 없음). foundation을 새 코드로 다시 apply하세요"
  other_db="$(fjson db_name 2>/dev/null || echo app)"
  lg="$(fjson db_provisioner_log_group)"; host="$(fjson db_host)"; port="$(fjson db_port)"
  admin_user="$(fjson db_admin_username)"; admin_arn="$(fjson db_admin_password_parameter_arn)"
  app_arn="$(db_param_arn "$id")"; app_db="$(db_name_of "$id")"
  # Git Bash는 /로 시작하는 환경 변수 값(로그 그룹 이름 등)을 Windows 경로로 바꾼다. 이 변환을 끄지 않으면
  # awslogs-group이 C:/Program Files/Git/... 로 들어가 작업 정의 등록이 실패한다. 변환 끄기는 이 호출에만 적용한다
  MSYS_NO_PATHCONV=1 MODE="$mode" ID="$id" FAMILY="$family" RGN="$rgn" ECR="$ecr" EXEC="$exec_role" LG="$lg" \
  DB_HOST="$host" DB_PORT="$port" DB_ADMIN_USER="$admin_user" ADMIN_PW_ARN="$admin_arn" APP_PARAM_ARN="$app_arn" \
  APP_DB="$app_db" OTHER_DB="$other_db" TOOLS_TAG="$TOOLS_TAG" \
  "$PY" - <<'PYEOF'
import json, os, sys
e = os.environ
mode = e["MODE"]
head = r'''set -u
export MYSQL_PWD
PW=$(printf '%s' "${APP_URL:-}" | sed -E 's#^mysql://[^:]+:([^@]+)@.*#\1#')
M="-h $DB_HOST -P $DB_PORT"
'''
scripts = {
    "provision": head + r'''MYSQL_PWD="$DB_ADMIN_PASSWORD"
mysql $M -u "$DB_ADMIN_USER" <<SQL || exit 1
CREATE DATABASE IF NOT EXISTS \`$APP_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS '$APP_DB'@'%' IDENTIFIED BY '$PW';
ALTER USER '$APP_DB'@'%' IDENTIFIED BY '$PW';
GRANT ALL PRIVILEGES ON \`$APP_DB\`.* TO '$APP_DB'@'%';
SQL
echo PROVISION_OK
''',
    "drop": head + r'''MYSQL_PWD="$DB_ADMIN_PASSWORD"
mysql $M -u "$DB_ADMIN_USER" <<SQL || exit 1
DROP DATABASE IF EXISTS \`$APP_DB\`;
DROP USER IF EXISTS '$APP_DB'@'%';
SQL
echo DROP_OK
''',
    "verify": head + r'''MYSQL_PWD="$PW"
if mysql $M -u "$APP_DB" -e "SELECT 1" "$APP_DB" >/dev/null 2>/tmp/e1; then echo OWN_DB_OK; else cat /tmp/e1; echo OWN_DB_FAIL; exit 2; fi
if mysql $M -u "$APP_DB" -e "USE \`$OTHER_DB\`" >/dev/null 2>/tmp/e2; then echo OTHER_DB_ACCESSIBLE; exit 3; else cat /tmp/e2; echo OTHER_DB_DENIED; fi
if mysql $M -u "$APP_DB" -e "SELECT COUNT(*) FROM mysql.user" >/dev/null 2>/tmp/e3; then echo SYSTEM_TABLES_ACCESSIBLE; exit 4; else cat /tmp/e3; echo SYSTEM_TABLES_DENIED; fi
DBS=$(mysql $M -u "$APP_DB" -N -e "SHOW DATABASES" 2>/tmp/e4) || { cat /tmp/e4; echo SHOW_DB_FAIL; exit 5; }
echo "VISIBLE_DBS: $(echo $DBS | tr '\n' ' ')"
EXTRA=$(echo "$DBS" | grep -v -x -e information_schema -e performance_schema -e "$APP_DB" || true)
if [ -n "$EXTRA" ]; then echo "OTHER_DBS_VISIBLE: $EXTRA"; exit 6; fi
echo OTHER_APP_DBS_HIDDEN
echo VERIFY_OK
''',
}
secrets = []
if mode in ("provision", "drop"):
    secrets.append({"name": "DB_ADMIN_PASSWORD", "valueFrom": e["ADMIN_PW_ARN"]})
if mode in ("provision", "verify"):
    secrets.append({"name": "APP_URL", "valueFrom": e["APP_PARAM_ARN"]})
env = [
    {"name": "DB_HOST", "value": e["DB_HOST"]},
    {"name": "DB_PORT", "value": e["DB_PORT"]},
    {"name": "DB_ADMIN_USER", "value": e["DB_ADMIN_USER"]},
    {"name": "APP_DB", "value": e["APP_DB"]},
    {"name": "OTHER_DB", "value": e["OTHER_DB"]},
]
td = {
    "family": e["FAMILY"],
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "256",
    "memory": "512",
    "executionRoleArn": e["EXEC"],
    "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
    "containerDefinitions": [{
        "name": "dbinit",
        "image": e["ECR"] + ":" + e["TOOLS_TAG"],
        "essential": True,
        "entryPoint": ["/bin/sh", "-c"],
        "command": [scripts[mode]],
        "environment": env,
        "secrets": secrets,
        "logConfiguration": {"logDriver": "awslogs", "options": {
            "awslogs-group": e["LG"], "awslogs-region": e["RGN"], "awslogs-stream-prefix": "dbinit"}},
    }],
}
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(td))
PYEOF
}

# foundation의 DB 작업용 Lambda로 DB 작업(provision | drop | verify)을 실행한다. Fargate 작업(db_task 아래쪽)과 같은 일을
# 몇 초에 끝낸다(Fargate는 컨테이너를 띄우는 데만 약 70초). 결과 로그의 마커(PROVISION_OK, VERIFY_OK 등)는 같다
db_lambda() {  # db_lambda <mode> <id> <함수 이름>
  local mode="$1" id="$2" fn="$3" rgn tmp status ok logtxt PY; PY="$(pick_python)"
  rgn="$(region)"; tmp="$(mktemp -d)"
  printf '{"mode":"%s","id":"%s"}' "$mode" "$id" > "$tmp/payload.json"
  log "DB 작업($mode) 실행 중 (Lambda)..."
  status="$(awsn lambda invoke --region "$rgn" --function-name "$fn" --cli-binary-format raw-in-base64-out \
    --payload "file://$(file_uri_path "$tmp/payload.json")" --query 'FunctionError' --output text \
    "$(file_uri_path "$tmp/out.json")" 2>"$tmp/err")" \
    || { local e; e="$(head -c 300 "$tmp/err" | mask)"; rm -rf "${tmp:?}"; die "DB 작업 Lambda를 호출하지 못했습니다: $e"; }
  ok="$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print('1' if d.get('ok') is True else '0')" "$tmp/out.json" 2>/dev/null || echo 0)"
  logtxt="$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print(d.get('log') or d.get('errorMessage') or '')" "$tmp/out.json" 2>/dev/null | tr -d '\r' | mask || true)"
  rm -rf "${tmp:?}"
  [ -n "$logtxt" ] && printf '%s\n' "$logtxt" | sed 's/^/    | /' >&2
  if [ "$ok" != "1" ] || { [ -n "$status" ] && [ "$status" != "None" ]; }; then
    die "DB 작업($mode)이 실패했습니다 (Lambda). 위 로그를 확인하세요"
  fi
  DB_TASK_LOG="$logtxt"
}

# 1회성 DB 작업을 실행하고 종료 코드를 확인한다. 로그(비밀 마스킹)는 표준 오류로 보여 준다.
# foundation에 DB 작업용 Lambda가 있으면 그것을 쓰고(빠름), 없으면(옛 foundation, NAT를 끈 구성) Fargate 작업으로 한다.
# PAVED_DB_VIA_FARGATE=1 이면 Lambda가 있어도 Fargate로 한다
db_task() {
  local fn; fn="$(fjson db_provisioner_lambda_name 2>/dev/null || true)"
  if [ -n "$fn" ] && [ "$fn" != "None" ] && [ -z "${PAVED_DB_VIA_FARGATE:-}" ]; then
    db_lambda "$1" "$2" "$fn"
    return
  fi
  db_task_fargate "$@"
}

db_task_fargate() {
  local mode="$1" id="$2" rgn cluster td_arn task_arn code subnets sg pub tid events why
  rgn="$(region)"; cluster="$(fjson cluster_name)"
  ensure_tools_image
  td_arn="$(awsn ecs register-task-definition --region "$rgn" --cli-input-json "$(db_taskdef_json "$mode" "$id")" \
    --query 'taskDefinition.taskDefinitionArn' --output text)" || die "DB 작업 정의를 등록하지 못했습니다"
  subnets="$(fjson task_subnet_ids | tr -d '[]" ' )"
  sg="$(fjson db_provisioner_security_group)"
  pub="DISABLED"; grep -Eq '"assign_public_ip": *true' "$FOUNDATION_JSON" && pub="ENABLED"
  task_arn="$(awsn ecs run-task --region "$rgn" --cluster "$cluster" --launch-type FARGATE --task-definition "$td_arn" \
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sg],assignPublicIp=$pub}" \
    --query 'tasks[0].taskArn' --output text)" || die "DB 작업을 시작하지 못했습니다"
  [ -n "$task_arn" ] && [ "$task_arn" != "None" ] || die "DB 작업을 시작하지 못했습니다"
  log "DB 작업($mode) 실행 중..."
  aws ecs wait tasks-stopped --region "$rgn" --cluster "$cluster" --tasks "$task_arn" || true
  code="$(aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks "$task_arn" \
    --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null || echo "")"
  # 이미지 pull·시크릿 조회 실패처럼 컨테이너가 시작되지 못하면 종료 코드가 없고 이유는 stoppedReason에만 있다
  why="$(aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks "$task_arn" \
    --query 'tasks[0].[stoppedReason,containers[0].reason]' --output text 2>/dev/null | tr '\t' ' ' | mask || true)"
  tid="${task_arn##*/}"
  events="$(awsn logs get-log-events --region "$rgn" --log-group-name "$(fjson db_provisioner_log_group)" \
    --log-stream-name "dbinit/dbinit/$tid" --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | mask || true)"
  [ -n "$events" ] && printf '%s\n' "$events" | sed 's/^/    | /' >&2
  awsn ecs deregister-task-definition --region "$rgn" --task-definition "$td_arn" >/dev/null 2>&1 || true
  if [ "$code" != "0" ]; then
    { [ "$code" != "None" ] && [ -n "$code" ]; } || code="없음(컨테이너가 시작되지 못함)"
    die "DB 작업($mode)이 실패했습니다 (종료 코드 $code). 사유: ${why:-알 수 없음}. 위 로그를 확인하세요"
  fi
  DB_TASK_LOG="$events"
}

random_password() {
  "$(pick_python)" -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
}

# 비밀 값을 SSM SecureString으로 저장한다. 값을 명령줄 인자로 넘기면 실행 중 프로세스 목록에 보이므로 임시 파일(file://)로 넘긴다.
# 임시 파일은 서브셸의 EXIT·신호 trap으로 지운다. 저장 도중 Ctrl-C를 누르거나 프로세스가 종료돼도 비밀번호가 든 파일이 남지 않는다
# (부모 셸의 trap과 섞이지 않게 서브셸 안에서 처리한다)
put_secret_param() {  # put_secret_param <리전> <이름> <설명> <값>
  (
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp:?}"' EXIT
    trap 'exit 130' INT TERM HUP
    umask 077
    printf '%s' "$4" > "$tmp/value"
    awsn ssm put-parameter --region "$1" --name "$2" --type SecureString --overwrite \
      --description "$3" --value "file://$(file_uri_path "$tmp/value")" >/dev/null
  )
}

# 앱 전용 DB·계정을 만들고 접속 정보를 SSM에 저장한다. apply 직전(승인 후)에만 실행한다
db_provision() {
  local id="$1" d name pw host port url rgn
  d="$(deploy_dir "$id")"; name="$(db_name_of "$id")"; rgn="$(region)"
  host="$(fjson db_host)"; port="$(fjson db_port)"; pw="$(random_password)"
  url="mysql://${name}:${pw}@${host}:${port}/${name}"
  log "앱 전용 DB 준비: $name"
  put_secret_param "$rgn" "$(db_param_name "$id")" "Per-app DATABASE_URL for deployment $id" "$url" \
    || die "접속 정보를 SSM에 저장하지 못했습니다"
  awsn ssm add-tags-to-resource --region "$rgn" --resource-type Parameter --resource-id "$(db_param_name "$id")" \
    --tags Key=Project,Value=paved-clouds Key=DeployId,Value="$id" Key=ManagedBy,Value=paved-clouds-platform >/dev/null 2>&1 || true
  unset pw url
  db_task provision "$id"
  : > "$d/db-provisioned"
  rm -f "$d/init.done"   # 새로 만든 DB에는 아직 테이블이 없다
}

cmd_db_check() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: db-check <id>"
  need aws
  local d; d="$(existing_dir "$id")"
  [ -f "$d/db-isolated" ] || die "이 배포는 앱 전용 DB를 쓰지 않습니다(--shared-db 이거나 DB 미사용)"
  db_task verify "$id"
  printf '%s' "$DB_TASK_LOG" | grep -q VERIFY_OK || die "격리 확인에 실패했습니다"
  log "앱 전용 계정은 자기 DB($(db_name_of "$id"))만 쓸 수 있습니다. 공유 DB, 시스템 테이블이 막혀 있고 다른 앱의 DB는 보이지도 않습니다"
}

cmd_drop_db() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: drop-db <id> [--yes]"; shift || true
  [ "${1:-}" = "--yes" ] && ASSUME_YES=1
  need aws
  local d; d="$(existing_dir "$id")"
  [ -f "$d/db-isolated" ] || die "이 배포는 앱 전용 DB를 쓰지 않습니다"
  confirm "앱 전용 DB $(db_name_of "$id") 와 계정을 지웁니다. 데이터는 복구할 수 없습니다. 계속할까요?"
  db_drop "$id"
}

db_drop() {
  local id="$1" d; d="$(deploy_dir "$id")"
  db_task drop "$id"
  awsn ssm delete-parameter --region "$(region)" --name "$(db_param_name "$id")" >/dev/null 2>&1 || true
  rm -f "$d/db-isolated" "$d/db-provisioned"
  log "앱 전용 DB와 접속 정보를 지웠습니다"
}

# --- 이력과 시도 기록 ----------------------------------------------------------------------------
# history.log   헬스체크를 통과한 배포만. 한 줄 = 시각|이미지|스냅샷 이름. 롤백이 여기서 대상을 고른다.
#               스냅샷(history/<이름>.app.json, .platform.json)에는 그때의 앱 설정과 이미지·아키텍처·유예 시간이 들어 있어서
#               롤백이 이미지만이 아니라 설정도 함께 되돌린다 (AGENTS.md 6장: 정상 동작이 확인된 이미지와 배포 설정을 기록한다)
# attempts.log  성공·실패를 가리지 않는 모든 시도. 한 줄 = 시각|결과(ok/fail)|이미지|사유.
#               실패한 배포도 기록해서 status, diagnose, 감사에 쓴다 (AGENTS.md 5장 10단계)
record_attempt() {  # record_attempt <디렉터리> <ok|fail> <이미지> [사유]
  local d="$1" result="$2" image="$3" reason="${4:-}"
  reason="${reason//|/ }"; reason="${reason//$'\n'/ }"
  printf '%s|%s|%s|%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$result" "$image" "$reason" >> "$d/attempts.log"
}

# 계획(plan)을 만들 때의 입력을 고정해 둔다(plan.app.json, plan.platform.json).
# 승인은 이 입력으로 만든 저장 plan에 대한 것이다. apply 뒤의 이력과 롤백 기준은 그 사이에 바뀔 수 있는
# 현재 입력 파일(*.auto.tfvars.json)이 아니라 이 고정본으로 남긴다
pin_plan_inputs() {
  cp "$1/app.auto.tfvars.json" "$1/plan.app.json"
  cp "$1/platform.auto.tfvars.json" "$1/plan.platform.json"
}

unpin_plan_inputs() { rm -f "$1/plan.app.json" "$1/plan.platform.json"; }

# 고정한 입력이 그대로인지 확인한다. 다르면 승인받은 계획과 지금 입력이 달라진 것이라 적용하지 않는다
check_plan_inputs() {
  local d="$1" f
  for f in app platform; do
    [ -f "$d/plan.$f.json" ] || die "계획을 만들 때의 입력 보관본(plan.$f.json)이 없습니다. 이전 버전이 만든 계획일 수 있으니 --plan-only 로 계획을 다시 만드세요"
    cmp -s "$d/plan.$f.json" "$d/$f.auto.tfvars.json" \
      || die "계획을 만든 뒤 입력 파일($f.auto.tfvars.json)이 바뀌었습니다. 승인받은 계획과 달라서 적용하지 않습니다. --plan-only 로 계획을 다시 만들어 승인받으세요"
  done
}

# 지금 정상으로 확인된 입력(계획을 만들 때 고정한 것)을 보관하고 스냅샷 이름을 출력한다
snapshot_healthy() {
  local d="$1" stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$d/history"
  cp "$d/plan.app.json" "$d/history/$stamp.app.json"
  cp "$d/plan.platform.json" "$d/history/$stamp.platform.json"
  echo "$stamp"
}

record_healthy() {
  local d="$1" image stamp; image="$(tf "$d" output -raw image)"
  stamp="$(snapshot_healthy "$d")"
  printf '%s|%s|%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$image" "$stamp" >> "$d/history.log"
}

# 지금 실제로 배포돼 있는 이미지(Terraform 상태 기준). 실패한 업데이트는 이력에 없지만 상태에는 있다.
# 이력의 마지막 줄로 정하면 실패한 업데이트 뒤에 마지막 정상 이미지를 "현재"로 착각해 롤백 대상에서 빼 버린다
current_image() { tf "$1" output -raw image 2>/dev/null; }

# 마지막으로 apply한 입력을 보관한다(apply_saved가 terraform apply 직후에 부른다). "지금 배포된 것"의 설정 기준이 된다.
# 입력 파일(app.auto.tfvars.json)은 --plan-only로 바꿔 놓고 적용하지 않은 값일 수 있어서 기준으로 삼을 수 없다
save_applied() {
  cp "$1/plan.app.json" "$1/applied.app.json"
  cp "$1/plan.platform.json" "$1/applied.platform.json"
}

image_in_file() { grep -o '"image": *"[^"]*"' "$1" | head -1 | sed -E 's/.*: *"//; s/"$//'; }

# 배포 항목의 지문: 이미지와 앱 설정이 같으면 같은 값. 이미지는 그대로 두고 포트·헬스체크·환경 변수만 바꾼 업데이트도 구분한다
fingerprint() {  # fingerprint <app json 파일> <이미지>
  "$(pick_python)" -c "import json,sys,hashlib;a=json.load(open(sys.argv[1],encoding='utf-8')).get('app',{});print(hashlib.sha256((sys.argv[2]+json.dumps(a,sort_keys=True)).encode()).hexdigest()[:16])" "$1" "$2"
}

# 롤백 대상 항목("이미지|스냅샷 이름")을 출력한다: 지금 배포된 것과 (이미지, 앱 설정)이 다른 가장 최근의 정상 항목.
# 이미지만 비교하면 앱 설정만 바꾼 업데이트(이미지가 같은 항목이 여러 개)에서 대상을 못 찾거나 엉뚱한 옛 이미지를 고른다.
# "지금 배포된 것"은 이력의 마지막 줄이 아니라 마지막으로 apply한 입력이다(실패한 업데이트는 이력에 없지만 배포돼 있다).
# 보관본이 없는 옛 배포는 Terraform 상태의 이미지로 이미지끼리만 비교한다
previous_entry() {
  local d="$1" mode cur ts image stamp efp
  if [ -f "$d/applied.app.json" ] && [ -f "$d/applied.platform.json" ]; then
    mode=config; cur="$(fingerprint "$d/applied.app.json" "$(image_in_file "$d/applied.platform.json")")"
  else
    mode=image; cur="$(current_image "$d")"
  fi
  # tac은 macOS에 없어서 awk로 줄 순서를 뒤집는다(최근 항목부터 본다)
  awk '{ l[NR] = $0 } END { for (i = NR; i > 0; i--) print l[i] }' "$d/history.log" 2>/dev/null | while IFS='|' read -r ts image stamp; do
    if [ "$mode" = config ]; then
      if [ -n "$stamp" ] && [ -f "$d/history/$stamp.app.json" ]; then
        efp="$(fingerprint "$d/history/$stamp.app.json" "$image")"
      else
        efp="legacy:$image"
      fi
      [ "$efp" != "$cur" ] || continue
    else
      [ "$image" != "$cur" ] || continue
    fi
    printf '%s|%s\n' "$image" "$stamp"
    break
  done
}

# 롤백 대상 이미지(previous_entry의 앞부분)
previous_image() { previous_entry "$1" | cut -d'|' -f1; }

# 그 이미지가 마지막으로 정상이었던 때의 스냅샷 이름(없으면 빈 값: 스냅샷 도입 전 이력)
snapshot_of_image() {
  local d="$1" image="$2"
  awk -F'|' -v img="$image" '$2 == img && $3 != "" { s = $3 } END { print s }' "$d/history.log" 2>/dev/null
}

# 스냅샷의 설정으로 되돌린다: 앱 설정 전체와 이미지·아키텍처·유예 시간.
# foundation 값은 지금 것을 유지한다(스냅샷 시점의 값은 낡았을 수 있다)
restore_snapshot() {
  local d="$1" stamp="$2" PY; PY="$(pick_python)"
  [ -f "$d/history/$stamp.app.json" ] && [ -f "$d/history/$stamp.platform.json" ] || die "스냅샷이 없습니다: $stamp"
  cp "$d/history/$stamp.app.json" "$d/app.auto.tfvars.json"
  "$PY" - "$d/platform.auto.tfvars.json" "$d/history/$stamp.platform.json" <<'PYEOF'
import json, sys
cur_p, snap_p = sys.argv[1], sys.argv[2]
cur = json.load(open(cur_p, encoding="utf-8"))
snap = json.load(open(snap_p, encoding="utf-8"))
for k in ("image", "cpu_architecture", "health_check_grace_seconds"):
    if k in snap["platform"]:
        cur["platform"][k] = snap["platform"][k]
json.dump(cur, open(cur_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
}

# 이번에 시도하는 이미지(입력 파일 기준)
input_image() { image_in_file "$1/platform.auto.tfvars.json"; }

# 이미지가 ECR에 실제로 있는지 확인한다. 없으면 태스크가 이미지를 받지 못해 서킷 브레이커까지 8분 넘게 기다리게 되고,
# 롤백 대상 이미지가 보관 개수 제한으로 지워졌을 때도 같은 일이 생긴다
check_image_exists() {
  local image="$1" rest repo ref rgn out
  rgn="$(region)"
  rest="${image#*/}"                      # <저장소 경로>:<태그> 또는 <저장소 경로>@sha256:...
  case "$rest" in
    *@sha256:*) repo="${rest%%@*}"; ref="imageDigest=${rest#*@}" ;;
    *:*) repo="${rest%:*}"; ref="imageTag=${rest##*:}" ;;
    *) die "이미지 이름을 해석하지 못했습니다: $image" ;;
  esac
  if out="$(aws ecr describe-images --region "$rgn" --repository-name "$repo" --image-ids "$ref" 2>&1 >/dev/null)"; then
    return 0
  fi
  if printf '%s' "$out" | grep -q 'ImageNotFoundException'; then
    die "ECR에 이미지가 없습니다: $image (태그가 틀렸거나 보관 개수 제한으로 지워졌습니다). 롤백이라면 --to 로 다른 이미지를 지정하세요"
  fi
  die "ECR에서 이미지를 확인하지 못했습니다: $(printf '%s' "$out" | head -c 200)"
}

# 계획만 만들고 적용하지 않은 변경이 다음 계획에 섞이지 않게, 입력을 마지막으로 apply한 값(applied.*)으로 되돌린다.
# 입력 파일은 --plan-only만 해도 바뀌므로(이미지, 앱 설정, 롤백 스냅샷, 앱 전용 DB 표식) 새 변경은 항상 배포된 상태에서 시작한다.
# apply된 적이 없는 배포(up --plan-only 직후)는 그대로 둔다
reset_inputs() {
  local d="$1" arn
  [ -f "$d/applied.app.json" ] && [ -f "$d/applied.platform.json" ] || return 0
  cp "$d/applied.app.json" "$d/app.auto.tfvars.json"
  cp "$d/applied.platform.json" "$d/platform.auto.tfvars.json"
  rm -f "$d/tfplan" "$d/plan.json"; unpin_plan_inputs "$d"
  arn="$(jget "$d/applied.platform.json" platform database_url_parameter_arn 2>/dev/null || true)"
  if [ -n "$arn" ] && [ "$arn" != "None" ]; then
    [ -f "$d/db-isolated" ] || printf '%s\n' "$(db_name_of "$(basename "$d")")" > "$d/db-isolated"
  elif [ -f "$d/db-isolated" ] && [ ! -f "$d/db-provisioned" ]; then
    rm -f "$d/db-isolated"    # 버린 계획이 켠 앱 전용 DB 표식
  fi
}

# 입력의 foundation 값을 지금 foundation 출력으로 갱신한다. up 때 복사한 값을 계속 쓰면 foundation을 다시 apply한 뒤
# (HTTPS 추가, ALB·보안 그룹 재생성) update·rollback이 사라진 리소스를 가리킨다
refresh_foundation() {
  local d="$1"
  export_foundation
  "$(pick_python)" - "$d/platform.auto.tfvars.json" "$FOUNDATION_JSON" <<'PYEOF'
import json, sys
p, f = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
d["platform"]["foundation"] = json.load(open(f, encoding="utf-8"))
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
}

# 입력 파일(platform.auto.tfvars.json)의 최상위 platform 필드 하나를 바꾼다
set_platform_field() {  # set_platform_field <디렉터리> <키> <값>
  "$(pick_python)" - "$1/platform.auto.tfvars.json" "$2" "$3" <<'PYEOF'
import json, sys
p, k, v = sys.argv[1:4]
d = json.load(open(p, encoding="utf-8"))
d["platform"][k] = v
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
}

# app 설정 파일이 DB를 쓰는지(true/false)
app_uses_db() {
  "$(pick_python)" -c "import json,sys;print(str(bool(json.load(open(sys.argv[1],encoding='utf-8')).get('use_database'))).lower())" "$1"
}

# 업데이트로 앱이 DB를 쓰기 시작하면 앱 전용 DB를 쓰도록 전환한다. 이걸 안 하면 모듈이 foundation의 공유 URL(DB 관리자 계정)로
# 대체해서, 앱 전용 DB가 기본이라는 약속과 달리 조용히 모든 DB에 대한 관리자 권한이 붙는다.
# 처음부터 --shared-db로 만든 배포(db-shared)는 그 선택을 존중한다. DB는 승인 후 apply 때 만든다
ensure_db_isolation() {
  local d="$1" id="$2" app="$3"
  [ "$(app_uses_db "$app")" = "true" ] || return 0
  [ ! -f "$d/db-isolated" ] || return 0
  [ ! -f "$d/db-shared" ] || return 0
  export_foundation
  printf '%s\n' "$(db_name_of "$id")" > "$d/db-isolated"
  set_platform_field "$d" database_url_parameter_arn "$(db_param_arn "$id")"
  log "앱이 DB를 쓰기 시작해서 앱 전용 DB($(db_name_of "$id"))를 쓰도록 전환합니다(접속 정보는 승인 후 apply 때 만듭니다)"
}

# 이번 배포(PRIMARY)가 시작한 태스크 중 멈춘 것만 센다. 서비스 전체를 세면 이전 업데이트에서 교체된 태스크가
# 최근 1시간 동안 목록에 남아 있어서, 업데이트를 몇 번 하면 정상 배포도 "반복 종료"로 오판한다
count_stopped_tasks() {  # count_stopped_tasks <리전> <클러스터> <배포 ID>
  [ -n "${3:-}" ] && [ "$3" != "None" ] || { echo 0; return 0; }
  local out
  # 조회가 실패하면 0으로 대신하되 조용히 넘어가지 않는다. 이 집계가 항상 0이면 "반복 종료" 판정이 영영 동작하지 않기 때문이다
  if ! out="$(aws ecs list-tasks --region "$1" --cluster "$2" --started-by "$3" --desired-status STOPPED \
      --query 'length(taskArns)' --output text 2>&1)"; then
    log "경고: 정지된 태스크를 조회하지 못했습니다(이번 배포의 반복 종료 판정이 동작하지 않습니다): $(printf '%s' "$out" | head -c 160)"
    echo 0
    return 0
  fi
  echo "$out"
}

# 이 PC의 docker가 만드는 이미지의 CPU 아키텍처(ECS cpu_architecture 값). docker가 없거나 알 수 없으면 X86_64.
# 이미지와 ECS 태스크의 아키텍처가 다르면 태스크가 바로 죽는다
detect_arch() {
  local a
  a="$(docker info --format '{{.Architecture}}' 2>/dev/null | tr -d '\r' || true)"
  case "$a" in aarch64|arm64) echo ARM64 ;; *) echo X86_64 ;; esac
}

# 프로젝트 이름(한글 포함)에서 배포 ID(소문자·숫자 4~8자)를 만든다. 같은 이름은 항상 같은 ID다.
# 이름의 영문·숫자를 앞에 최대 4자 쓰고, 이름의 해시로 8자까지 채운다. 한글만 있는 이름도 서로 구분된다
make_id() {  # make_id <이름>
  [ -n "${1// /}" ] || die "이름이 비어 있습니다"
  "$(pick_python)" -c "
import hashlib, re, sys
n = sys.argv[1].strip()
slug = re.sub('[^a-z0-9]', '', n.lower())[:4]
print((slug + hashlib.sha256(n.encode('utf-8')).hexdigest())[:8])
" "$1"
}

# app 설정의 init_command(문자열 배열)를 한 줄 JSON으로 낸다. 없으면 아무것도 내지 않는다
init_command_json() {  # init_command_json <app tfvars json>
  "$(pick_python)" -c "
import json, sys
a = json.load(open(sys.argv[1], encoding='utf-8')).get('app', {})
c = a.get('init_command') or []
if not isinstance(c, list) or not all(isinstance(x, str) and x for x in c) or len(c) > 10:
    sys.exit('init_command는 비어 있지 않은 문자열 배열(최대 10개)이어야 합니다')
if c:
    print(json.dumps(c, ensure_ascii=False))
" "$1"
}

# 앱 이미지로 초기화 명령(테이블 생성·마이그레이션)을 1회 실행한다. app 설정의 init_command를 쓰고 없으면 건너뛴다.
# 앱과 같은 작업 정의(같은 이미지·DATABASE_URL 비밀·네트워크)에 명령만 바꿔서 실행하므로 앱 DB 계정으로 돈다.
# 여러 번 실행돼도 안전해야 한다(예: CREATE TABLE IF NOT EXISTS). update·rollback의 적용마다 다시 실행된다
app_init_task() {  # app_init_task <배포 디렉터리> <id>
  local d="$1" cmd rgn cluster td sg lg subnets pub ov task_arn code why tid events
  cmd="$(init_command_json "$d/plan.app.json")" || die "app 설정의 init_command가 올바르지 않습니다"
  [ -n "$cmd" ] || return 0
  # 이미지와 명령이 직전에 성공한 초기화와 같으면(설정만 바꾸는 update 등) 다시 돌리지 않는다. 약 55초를 아끼고 비멱등 명령의 중복 실행을 피한다
  local fp; fp="$(printf '%s|%s' "$(image_in_file "$d/plan.platform.json")" "$cmd" | "$(pick_python)" -c "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())")"
  if [ -f "$d/init.done" ] && [ "$(cat "$d/init.done")" = "$fp" ]; then
    log "앱 초기화 작업 건너뜀: 같은 이미지·명령으로 이미 실행했습니다"
    return 0
  fi
  rgn="$(region)"; cluster="$(tf "$d" output -raw cluster_name)"
  td="$(tf "$d" output -raw task_definition_arn)" || die "task_definition_arn 출력이 없습니다(배포 템플릿이 옛 버전입니다)"
  sg="$(tf "$d" output -raw task_security_group_id)"; lg="$(tf "$d" output -raw log_group_name)"
  subnets="$(fjson task_subnet_ids | tr -d '[]" ' )"
  pub="DISABLED"; grep -Eq '"assign_public_ip": *true' "$FOUNDATION_JSON" && pub="ENABLED"
  ov="$d/init-overrides.json"
  printf '{"containerOverrides":[{"name":"app","command":%s}]}' "$cmd" > "$ov"
  log "앱 초기화 작업 실행: $cmd"
  task_arn="$(awsn ecs run-task --region "$rgn" --cluster "$cluster" --launch-type FARGATE --task-definition "$td" \
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sg],assignPublicIp=$pub}" \
    --overrides "file://$(file_uri_path "$ov")" --query 'tasks[0].taskArn' --output text)" \
    || { rm -f "$ov"; die "앱 초기화 작업을 시작하지 못했습니다"; }
  rm -f "$ov"
  [ -n "$task_arn" ] && [ "$task_arn" != "None" ] || die "앱 초기화 작업을 시작하지 못했습니다"
  # 작업이 멈출 때까지 기다린다. 제한 시간(INIT_TIMEOUT, 기본 300초)을 넘기면 작업을 멈춰서 계속 과금되거나 다음 시도와 겹치지 않게 한다
  local waited=0 limit="${INIT_TIMEOUT:-300}" poll="${INIT_POLL:-5}" st
  while :; do
    st="$(aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks "$task_arn" --query 'tasks[0].lastStatus' --output text 2>/dev/null | tr -d '' || true)"
    [ "$st" != "STOPPED" ] || break
    if [ "$waited" -ge "$limit" ]; then
      awsn ecs stop-task --region "$rgn" --cluster "$cluster" --task "$task_arn" --reason "paved-clouds: init timeout" >/dev/null 2>&1 || true
      die "앱 초기화 작업이 ${limit}초 안에 끝나지 않아 멈췄습니다(작업 상태: ${st:-알 수 없음}). init_command가 끝나지 않는 명령인지 확인하세요"
    fi
    sleep "$poll"; waited=$((waited + poll))
  done
  code="$(aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks "$task_arn" \
    --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null || echo "")"
  why="$(aws ecs describe-tasks --region "$rgn" --cluster "$cluster" --tasks "$task_arn" \
    --query 'tasks[0].[stoppedReason,containers[0].reason]' --output text 2>/dev/null | tr '\t' ' ' | mask || true)"
  tid="${task_arn##*/}"
  events="$(awsn logs get-log-events --region "$rgn" --log-group-name "$lg" --log-stream-name "app/app/$tid" \
    --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | mask || true)"
  [ -n "$events" ] && printf '%s\n' "$events" | sed 's/^/    | /' >&2
  if [ "$code" != "0" ]; then
    { [ "$code" != "None" ] && [ -n "$code" ]; } || code="없음(컨테이너가 시작되지 못함)"
    die "앱 초기화 작업이 실패했습니다 (종료 코드 $code). 사유: ${why:-알 수 없음}"
  fi
  printf '%s
' "$fp" > "$d/init.done"
  log "앱 초기화 작업 완료"
}

# ZIP을 폴더로 푼다. 경로 이탈(../, 절대 경로), 심볼릭 링크, 과도한 크기·파일 수를 거부한다(업로드는 신뢰하지 않는다).
# 압축 안에 최상위 폴더가 하나뿐이면(GitHub 아카이브) 그 폴더를 기준으로 삼는다. 기준 폴더를 출력한다
extract_zip() {  # extract_zip <zip> <대상 폴더>
  "$(pick_python)" - "$1" "$2" <<'PYEOF'
import os, stat, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
MAX_FILES = 20000
MAX_BYTES = int(os.environ.get("ZIP_MAX_BYTES", 1 << 30))   # 풀었을 때 총 크기 한도(기본 1 GiB)
MAX_RATIO = 200                                              # 파일 하나의 압축률 한도(압축 폭탄 차단)
os.makedirs(dst, exist_ok=True)
root = os.path.realpath(dst)
with zipfile.ZipFile(src) as z:
    infos = z.infolist()
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES:
        sys.exit("ZIP이 너무 큽니다(파일 수 또는 풀었을 때 크기 한도 초과)")
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
            sys.exit(f"ZIP에 허용되지 않는 경로가 있습니다: {name}")
        if i.flag_bits & 0x1:
            sys.exit("암호가 걸린 ZIP은 지원하지 않습니다")
        if stat.S_ISLNK(i.external_attr >> 16):
            sys.exit(f"ZIP에 심볼릭 링크가 있습니다: {name}")
        target = os.path.realpath(os.path.join(root, name))
        if target != root and not target.startswith(root + os.sep):
            sys.exit(f"ZIP 경로가 대상 폴더 밖을 가리킵니다: {name}")
        if i.file_size > (1 << 20) and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
            sys.exit(f"압축률이 비정상적으로 높은 파일이 있습니다(압축 폭탄 의심): {name}")
    # extractall 대신 직접 풀면서 실제로 쓴 바이트를 센다(헤더에 적힌 크기가 거짓이어도 한도를 지킨다)
    written = 0
    for i in infos:
        name = i.filename.replace("\\", "/")
        target = os.path.join(root, name)
        if i.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(i) as src_f, open(target, "wb") as out_f:
            while True:
                chunk = src_f.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    sys.exit("ZIP이 너무 큽니다(풀었을 때 크기 한도 초과)")
                out_f.write(chunk)
entries = [e for e in os.listdir(root) if e not in ("__MACOSX",)]
base = os.path.join(root, entries[0]) if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])) else root
print(base)
PYEOF
}

# 이 배포의 이미지 주소(ECR 주소:태그)를 미리 정해 출력한다. 계획은 이미지가 없어도 만들 수 있으므로(승인 뒤에 빌드),
# plan에 쓸 주소를 먼저 정하고 빌드할 때 같은 태그(build --tag)를 쓴다
cmd_image_ref() {  # image-ref <id>
  local id="${1:-}"; [ -n "$id" ] || die "사용법: image-ref <id>"
  valid_id "$id"; need aws
  export_foundation
  echo "$(fjson ecr_repository_url):$id-r$(date +%s)"
}

# foundation의 최신 출력(deploy_inputs)을 compact JSON 한 줄로 낸다. 읽기 전용이다(foundation.json 캐시도 갱신한다).
# worker가 비용을 계산하기 전에 지금 foundation 구성(가용 영역 수, NAT 유무)을 읽는 데 쓴다. 캐시 파일만 읽으면
# foundation을 다시 apply하기 전의 옛 구성으로 비용을 확정해 예산을 넘는 구성이 추천될 수 있다
cmd_foundation_info() {
  need terraform
  export_foundation
  "$(pick_python)" -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')),ensure_ascii=False,separators=(',',':')))" "$FOUNDATION_JSON"
}

# 소스(ZIP 또는 폴더)에서 이미지를 빌드해 foundation ECR에 올리고 이미지 주소를 표준 출력으로 낸다(진행 로그는 표준 오류).
# 승인된 배포에만 실행한다(AGENTS.md 7장: Docker 빌드·실행에는 배포 승인과 실행 범위를 적용한다).
# 빌드에는 호스트의 AWS 키나 docker 소켓을 넘기지 않는다
cmd_build() {
  local id="" source="" dockerfile="Dockerfile" tag="" arch=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="$2"; shift 2 ;;
      --source) source="$2"; shift 2 ;;
      --dockerfile) dockerfile="$2"; shift 2 ;;
      --tag) tag="$2"; shift 2 ;;
      --arch) arch="$2"; shift 2 ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  [ -n "$id" ] && [ -n "$source" ] || die "사용법: build --id <id> --source <ZIP|폴더> [--dockerfile 경로] [--tag 태그] [--arch X86_64|ARM64]"
  valid_id "$id"
  # 아키텍처는 계획(up --arch)과 같아야 한다. 계획은 ECS 태스크의 CPU 아키텍처로 고정되는데 이미지가 다르면 태스크가 바로 죽는다.
  # 생략하면 이 PC의 docker 아키텍처로 빌드한다(다른 아키텍처를 지정하면 docker가 에뮬레이션으로 빌드하므로 느리다)
  [ -z "$arch" ] || [ "$arch" = "X86_64" ] || [ "$arch" = "ARM64" ] || die "--arch는 X86_64 또는 ARM64여야 합니다: $arch"
  # 태그를 미리 정하면 승인 전에 이미지 주소(ECR 주소:태그)를 알 수 있어 plan을 먼저 만들 수 있다
  [ -z "$tag" ] || [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "--tag 형식이 올바르지 않습니다: $tag"
  case "$dockerfile" in /*|*..*) die "--dockerfile은 소스 안의 상대 경로여야 합니다: $dockerfile" ;; esac
  need docker; need aws
  export_foundation
  local rgn ecr registry work ctx arch platform image cfg
  rgn="$(region)"; ecr="$(fjson ecr_repository_url)"; registry="${ecr%%/*}"
  work="$(mktemp -d)"
  # docker 로그인 토큰(ECR, 12시간 유효)이 든 임시 폴더라서 Ctrl-C·종료 신호에도 지운다(put_secret_param과 같은 이유)
  trap "rm -rf '${work}'" EXIT
  trap 'exit 130' INT TERM HUP
  if [ -d "$source" ]; then
    ctx="$source"
  elif [ -f "$source" ]; then
    ctx="$(extract_zip "$source" "$work/src")" || { rm -rf "${work:?}"; die "ZIP을 풀지 못했습니다"; }
  else
    rm -rf "${work:?}"; die "소스를 찾을 수 없습니다: $source"
  fi
  [ -f "$ctx/$dockerfile" ] || { rm -rf "${work:?}"; die "Dockerfile을 찾지 못했습니다: $ctx/$dockerfile"; }
  [ -n "$arch" ] || arch="$(detect_arch)"
  platform="linux/amd64"; [ "$arch" = "ARM64" ] && platform="linux/arm64"
  [ -n "$tag" ] || tag="$id-r$(date +%s)"
  image="$ecr:$tag"
  log "이미지 빌드: $image ($platform)"
  cfg="$work/docker"; mkdir -p "$cfg"; echo '{}' > "$cfg/config.json"
  # 사용자의 docker 설정(자격증명 도우미)을 건드리지 않으려고 임시 설정 폴더를 쓴다
  # `( ... ) || ...` 안에서는 bash가 set -e를 꺼서 앞 명령이 실패해도 계속 진행하고 마지막 명령의 결과만 남는다.
  # 그래서 단계마다 직접 검사한다(빌드가 실패했는데 push가 성공해 옛 이미지가 배포되는 것을 막는다)
  (
    set -o pipefail
    export DOCKER_CONFIG; DOCKER_CONFIG="$(native_path "$cfg")"
    # 이 PC에 같은 태그의 옛 이미지가 남아 있으면 빌드가 실패해도 그것이 push될 수 있어 태그를 먼저 지운다
    docker rmi -f "$image" >/dev/null 2>&1 || true
    docker build --platform "$platform" -f "$(native_path "$ctx/$dockerfile")" -t "$image" "$(native_path "$ctx")" >&2 || exit 1
    aws ecr get-login-password --region "$rgn" | docker login --username AWS --password-stdin "$registry" >/dev/null || exit 1
    docker push "$image" >&2 || exit 1
  ) || { rm -rf "${work:?}"; die "이미지를 빌드하거나 올리지 못했습니다"; }
  rm -rf "${work:?}"
  log "이미지를 올렸습니다. 아키텍처: $arch"
  echo "$image"
}

# plan 저장 → 승인 → 저장된 plan 그대로 apply → 헬스체크 대기
plan_confirm_apply() {
  local d="$1" id="$2"
  # 이전 계획과 그 입력 보관본을 먼저 지운다. 새 plan이 실패했을 때 옛 tfplan이 새 입력과 짝이 맞지 않은 채 남지 않게 한다
  rm -f "$d/tfplan" "$d/plan.json"; unpin_plan_inputs "$d"
  pin_plan_inputs "$d"
  log "plan"
  tf "$d" plan -input=false -out=tfplan -no-color | tail -n 40
  tf "$d" show -json tfplan > "$d/plan.json"
  check_plan_inputs "$d"   # plan을 만드는 동안 입력이 바뀌지 않았는지
  log "승인 화면용 계획 저장: $d/plan.json"

  if [ "${PLAN_ONLY:-0}" = "1" ]; then
    log "계획만 만들었습니다. 승인 후 적용: deploy.sh apply $id"
    return 0
  fi
  confirm "위 계획대로 apply 할까요?"
  apply_saved "$d" "$id"
}

# 저장된 plan(tfplan)만 적용한다. 새 plan을 만들지 않는다.
# 성공이든 실패든 시도를 attempts.log에 남긴다. 정상 확인된 것만 history.log와 스냅샷에 남긴다
apply_saved() {
  local d="$1" id="$2" image
  [ -f "$d/tfplan" ] || die "저장된 계획이 없습니다. 먼저 --plan-only 로 계획을 만드세요"
  # 승인받은 계획을 만들 때의 입력과 지금 입력이 같은지 확인한다. 다르면 이력이 실제 적용된 것과 다른 설정을 롤백 기준으로 저장한다
  check_plan_inputs "$d"
  UP_PHASE=""   # 여기서부터는 state가 생길 수 있어 폴더를 지우지 않는다
  image="$(image_in_file "$d/plan.platform.json")"
  # 계획 단계에서는 이미지가 없어도 되므로(승인 뒤에 빌드·푸시), 적용 직전에 ECR에 실제로 있는지 확인한다.
  # 없으면 태스크가 이미지를 받지 못해 서킷 브레이커까지 8분 넘게 기다리게 된다
  export_foundation
  if ! ( check_image_exists "$image" ); then
    record_attempt "$d" fail "$image" "ECR에 이미지가 없음"
    return 1
  fi
  # 앱 전용 DB는 승인된 뒤에만 만든다 (계획 단계에서는 접속 정보 ARN만 쓰고 아무것도 만들지 않는다)
  if [ -f "$d/db-isolated" ] && [ ! -f "$d/db-provisioned" ]; then
    export_foundation
    if ! ( db_provision "$id" ); then
      record_attempt "$d" fail "$image" "앱 전용 DB 준비 실패"
      return 1
    fi
  fi
  log "apply (저장된 계획)"
  if ! tf "$d" apply -input=false -no-color tfplan | tail -n 15; then
    record_attempt "$d" fail "$image" "terraform apply 실패"
    return 1
  fi
  save_applied "$d"
  tf "$d" output -json > "$d/outputs.json"
  # 한 번 적용한 계획은 다시 쓸 수 없다. 남겨 두면 오해를 부르니 지운다
  rm -f "$d/tfplan"

  # 앱 초기화 작업(테이블 생성 등). /health가 DB 연결만 확인하는 앱은 테이블이 없어도 헬스체크를 통과해서,
  # 이 단계가 없으면 "배포 성공"인데 실제 기능은 500 오류가 나는 상태가 된다
  if ! ( app_init_task "$d" "$id" ); then
    record_attempt "$d" fail "$image" "앱 초기화 작업 실패"
    log "앱 초기화 작업이 실패했습니다. 원인: deploy.sh diagnose $id"
    unpin_plan_inputs "$d"
    return 1
  fi

  local healthy=0
  wait_healthy "$id" && healthy=1
  if [ "$healthy" = 1 ]; then
    record_attempt "$d" ok "$image" ""
    record_healthy "$d"
  else
    record_attempt "$d" fail "$image" "${WAIT_REASON:-헬스체크 실패}"
    log "헬스체크 실패. 원인: deploy.sh diagnose $id"
  fi
  unpin_plan_inputs "$d"   # 이 계획은 적용이 끝났다(이력과 applied.*는 위에서 이 보관본으로 남겼다)
  [ "$healthy" = 1 ]
}

cmd_apply() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: apply <id>  (먼저 up/update/rollback --plan-only 로 계획을 만든다)"
  need terraform; need aws
  local d; d="$(existing_dir "$id")"
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  apply_saved "$d" "$id"
}

cmd_up() {
  local id="" name="" image="" port="auto" app="" grace="" shared_db=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="$2"; shift 2 ;;
      --name) name="$2"; shift 2 ;;
      --image) image="$2"; shift 2 ;;
      --port) port="$2"; shift 2 ;;
      --app) app="$2"; shift 2 ;;
      --arch) ARCH="$2"; shift 2 ;;
      --grace) grace="$2"; shift 2 ;;
      --shared-db) shared_db=1; shift ;;
      --skip-nat-check) SKIP_NAT_CHECK=1; shift ;;
      --yes) ASSUME_YES=1; shift ;;
      --plan-only) PLAN_ONLY=1; shift ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  [ -z "$id" ] || [ -z "$name" ] || die "--id 와 --name 은 함께 쓸 수 없습니다"
  [ -z "$name" ] || id="$(make_id "$name")"
  [ -n "$id" ] && [ -n "$image" ] && [ -n "$app" ] || die "--id(또는 --name) --image --app 이 모두 필요합니다"
  valid_id "$id"
  [ -n "${ARCH:-}" ] || ARCH="$(detect_arch)"
  [ "$ARCH" = "X86_64" ] || [ "$ARCH" = "ARM64" ] || die "--arch는 X86_64 또는 ARM64여야 합니다: $ARCH"
  [ "$port" = "auto" ] || [[ "$port" =~ ^[0-9]+$ ]] || die "--port는 숫자 또는 auto여야 합니다"
  [ -z "$grace" ] || [[ "$grace" =~ ^[0-9]+$ ]] || die "--grace는 숫자(초)여야 합니다"
  [ -f "$app" ] || die "app 파일이 없습니다: $app"
  need terraform; need aws

  local d; d="$(deploy_dir "$id")"
  claim_deploy_dir "$id"

  local rgn; rgn="$(region)"
  log "배포 $id 준비 (리전 $rgn)"

  export_foundation
  nat_preflight
  # 계획만 만들 때는 이미지가 아직 없어도 된다(승인 뒤에 빌드·푸시한다). 적용 직전(apply_saved)에 반드시 확인한다
  [ "${PLAN_ONLY:-0}" = "1" ] || check_image_exists "$image"

  # 배포 폴더를 먼저 만든다. 고른 포트를 이 폴더에 예약으로 남겨서, 승인 전에 계획만 만든 다른 배포가 같은 포트를 고르지 않게 한다.
  # 여기부터 apply 시작 전까지 실패하거나 취소하면 만들다 만 폴더를 지운다(cleanup_failed_up). 폴더가 지워지면 예약도 풀린다
  UP_DIR="$d"; UP_PHASE=prep
  trap cleanup_failed_up EXIT
  mkdir -p "$d"
  if [ "$port" = "auto" ]; then
    port="$(reserve_port "$id" "$d")" || die "허용 범위에 빈 포트가 없습니다"
    log "리스너 포트 자동 할당: $port"
  else
    # 지정한 포트를 다른 배포의 미적용 계획이 이미 예약했으면 apply에서야 DuplicateListener로 실패한다. 계획 단계에서 막는다
    local reserved_ports; reserved_ports="$(local_reserved_ports "$id")" || die "배포 폴더의 포트 예약을 읽지 못했습니다"
    case " $reserved_ports " in
      *" $port "*) die "포트 $port 는 이 PC의 다른 배포(미적용 계획 포함)가 이미 예약했습니다. 다른 포트를 지정하거나 --port auto 를 쓰세요" ;;
    esac
    printf '%s\n' "$port" > "$d/port.reserved"
  fi

  # 앱이 DB를 쓰면 기본으로 앱 전용 DB를 쓴다. --shared-db 이면 공유 DB(관리자 계정)를 쓴다
  local use_db param_arn=""
  use_db="$(app_uses_db "$app")"
  if [ "$use_db" = "true" ] && [ "$shared_db" = "0" ]; then
    param_arn="$(db_param_arn "$id")"
    log "앱 전용 DB를 사용합니다: $(db_name_of "$id") (접속 정보는 승인 후 apply 때 만듭니다)"
  elif [ "$use_db" = "true" ]; then
    log "경고: 공유 DB를 DB 관리자 계정으로 사용합니다(--shared-db)"
  fi

  # provider를 배포마다 다시 받지 않도록 캐시를 쓴다
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  mkdir -p "$TF_PLUGIN_CACHE_DIR"

  tar -C "$TEMPLATE" --exclude=.terraform -cf - . | tar -C "$d" -xf -
  [ -z "$param_arn" ] || printf '%s\n' "$(db_name_of "$id")" > "$d/db-isolated"
  # --shared-db를 고른 배포는 나중에 update로 앱 전용 DB로 몰래 바뀌지 않게 기억해 둔다
  if [ "$use_db" = "true" ] && [ "$shared_db" = "1" ]; then printf 'shared\n' > "$d/db-shared"; fi

  cat > "$d/platform.auto.tfvars.json" <<EOF
{"platform": {
  "region": "$rgn",
  "deploy_id": "$id",
  "image": "$image",
  "cpu_architecture": "${ARCH:-X86_64}",
  "listener_port": $port,
  "database_url_parameter_arn": "$param_arn",
  "health_check_grace_seconds": ${grace:-90},
  "foundation": $(cat "$FOUNDATION_JSON")
}}
EOF
  # LLM 출력(승인된 값)은 app 변수로 감싸서 그대로 둔다
  printf '{"app": %s}\n' "$(cat "$app")" > "$d/app.auto.tfvars.json"

  state_setup "$d" "$id"
  log "init"
  tf "$d" init -input=false >/dev/null
  plan_confirm_apply "$d" "$id"
  UP_PHASE=""
}

# 이미지나 앱 설정을 바꿔 다시 배포한다. 롤백도 같은 경로를 쓴다
redeploy() {
  local id="$1" image="$2" app="$3" d
  d="$(existing_dir "$id")"
  need terraform; need aws
  if [ -n "$image" ]; then
    # 입력 파일의 image 값만 바꾼다. 태그는 덮어쓸 수 없으니 새 태그를 쓴다
    # sed -i는 GNU와 BSD 문법이 다르고, 이미지 이름의 & 같은 문자가 치환 문법으로 해석되어서 JSON으로 고친다
    set_platform_field "$d" image "$image"
    [ "$(input_image "$d")" = "$image" ] || die "image 값을 바꾸지 못했습니다"
  fi
  if [ -n "$app" ]; then
    [ -f "$app" ] || die "app 파일이 없습니다: $app"
    printf '{"app": %s}\n' "$(cat "$app")" > "$d/app.auto.tfvars.json"
    ensure_db_isolation "$d" "$id" "$app"
  fi
  refresh_foundation "$d"
  # up과 달리 update·rollback의 이미지는 이미 있어야 한다(rollback 대상이 보관 개수 제한으로 지워졌다면 승인 전에 알린다)
  check_image_exists "$(input_image "$d")"
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
  reset_inputs "$(existing_dir "$id")"
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
  local d stamp entry; d="$(existing_dir "$id")"
  reset_inputs "$d"
  if [ -n "$to" ]; then
    stamp="$(snapshot_of_image "$d" "$to")"
  else
    entry="$(previous_entry "$d")"
    [ -n "$entry" ] || die "되돌릴 이전 정상 배포가 이력에 없습니다 ($d/history.log)"
    to="${entry%%|*}"; stamp="${entry#*|}"
  fi
  log "롤백 대상 이미지: $to"
  if [ -n "$stamp" ]; then
    # 이미지만 되돌리면, 그 사이 update --app으로 포트·헬스체크 경로·크기·환경 변수를 바꿨을 때 옛 이미지에 새 설정이 붙어 또 실패한다
    restore_snapshot "$d" "$stamp"
    log "그 이미지가 정상이던 때의 앱 설정도 함께 되돌립니다(스냅샷 $stamp)"
    log "주의: DB 스키마와 데이터는 되돌아가지 않습니다"
    redeploy "$id" "" ""
  else
    log "경고: 이 이미지에는 정상이던 때의 설정 스냅샷이 없어 이미지만 되돌립니다(스냅샷 도입 전 이력). 지금 앱 설정이 그대로 쓰입니다"
    log "주의: DB 스키마와 데이터는 되돌아가지 않습니다"
    redeploy "$id" "$to" ""
  fi
}

# 대상 그룹의 태스크가 healthy가 될 때까지 기다린다 (Terraform은 기다리지 않는다)
#   - ECS 배포가 COMPLETED이고 대상이 healthy일 때만 통과한다. 대상만 보면 롤링 교체 중 옛 태스크 때문에 일찍 통과한다
#   - 앱이 응답은 하는데 헬스체크 코드가 계속 틀리면(예: 경로 오류 404) 기다려도 소용없으니 실패로 확정한다(실측 apply 후 약 3분)
#   - 이번 배포의 태스크가 반복해서 죽거나 시간이 지나도 실패로 끝낸다. ECS 서킷 브레이커가 배포를 FAILED로 만드는 데는 더 걸린다
# 실패하면 WAIT_REASON에 사유를 남긴다(attempts.log에 기록된다)
wait_healthy() {
  local id="$1" d tg cluster svc start state stopped rollout depid reasons mismatch=0 rgn th dep
  WAIT_REASON=""
  d="$(deploy_dir "$id")"; rgn="$(region)"
  tg="$(tf "$d" output -raw target_group_arn)"
  cluster="$(tf "$d" output -raw cluster_name)"
  svc="$(tf "$d" output -raw service_name)"
  start=$(date +%s)
  log "헬스체크 대기 (최대 ${HEALTH_TIMEOUT}초)"
  while true; do
    # 대상 상태와 헬스체크 실패 설명(예: "Health checks failed with these codes: [404]")을 한 번에 읽는다. 한 줄이 대상 하나(탭 구분)
    th="$(aws elbv2 describe-target-health --region "$rgn" --target-group-arn "$tg" \
      --query 'TargetHealthDescriptions[].[TargetHealth.State,TargetHealth.Description]' --output text 2>/dev/null | tr -d '\r' || true)"
    state="$(printf '%s\n' "$th" | awk -F'\t' 'NF { printf "%s ", $1 }')"
    reasons="$(printf '%s\n' "$th" | awk -F'\t' 'NF { print $2 }')"
    # 현재(PRIMARY) 배포의 롤아웃 상태와 ID
    dep="$(aws ecs describe-services --region "$rgn" --cluster "$cluster" --services "$svc" \
      --query 'services[0].deployments[?status==`PRIMARY`]|[0].[rolloutState,id]' --output text 2>/dev/null | tr -d '\r' || true)"
    rollout="$(printf '%s' "$dep" | awk -F'\t' '{ print $1 }')"
    depid="$(printf '%s' "$dep" | awk -F'\t' '{ print $2 }')"
    log "대상=${state:-등록 대기} 배포=${rollout:-?}"
    if [ "$rollout" = "FAILED" ]; then
      WAIT_REASON="ECS 서킷 브레이커가 배포를 FAILED로 만들었습니다(태스크가 3번 실패)"
      log "ECS 배포가 실패했습니다 (서킷 브레이커)"
      return 1
    fi
    if [ "$rollout" = "COMPLETED" ]; then
      case "$state" in
        *unhealthy*|*draining*|*initial*) ;;
        *healthy*) log "정상입니다: $(tf "$d" output -raw url)"; return 0 ;;
      esac
    fi
    # 앱이 응답하는데 4xx(예: 경로 오류 404)면 기다려도 나아지지 않는 결정적인 실패다.
    # 5xx는 시작 중에 503을 돌려주는 앱이 있어서 즉시 실패로 보지 않는다
    if printf '%s' "$reasons" | grep -Eq 'codes: \[4[0-9][0-9]\]'; then
      mismatch=$((mismatch + 1))
    else
      mismatch=0
    fi
    if [ "$mismatch" -ge 4 ]; then
      WAIT_REASON="헬스체크가 4xx를 ${mismatch}회 연속 돌려줍니다. health_check_path를 확인하세요 ($(printf '%s' "$reasons" | head -c 120))"
      log "앱이 응답하지만 헬스체크가 4xx 코드를 돌려줍니다(${mismatch}회 연속). health_check_path가 틀렸을 가능성이 큽니다"
      log "ECS 서킷 브레이커가 배포를 FAILED로 확정하기까지는 더 걸리고, 그동안 태스크 교체를 반복합니다"
      return 1
    fi
    # 이번 배포의 태스크가 계속 뜨자마자 죽으면 시간 초과를 기다리지 않고 바로 실패로 본다
    stopped="$(count_stopped_tasks "$rgn" "$cluster" "$depid")"
    if [ "${stopped:-0}" -ge 3 ]; then
      WAIT_REASON="이번 배포의 태스크가 반복해서 종료되었습니다(${stopped}개)"
      log "태스크가 반복해서 종료되었습니다(이번 배포에서 ${stopped}개)"
      return 1
    fi
    if [ $(( $(date +%s) - start )) -ge "$HEALTH_TIMEOUT" ]; then
      WAIT_REASON="시간 초과(${HEALTH_TIMEOUT}초)"
      log "시간 초과"
      return 1
    fi
    sleep 15
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
  if [ -f "$d/attempts.log" ]; then
    echo "최근 시도(시각|결과|이미지|사유):"
    tail -n 5 "$d/attempts.log" | sed -E 's#\|[^|]*/#|#; s/^/  /'
  fi
  if [ -f "$d/db-isolated" ]; then echo "DB: 앱 전용 $(cat "$d/db-isolated")"; else echo "DB: 공유 또는 미사용"; fi
  # 한글 키를 --query에 쓰면 Windows Git Bash에서 aws가 JMESPath를 해석하지 못한다. 값만 받아 여기서 이름을 붙인다
  local st dc rc pc svc_out
  # 프로세스 치환(< <(...))은 aws 실패를 알리지 못해서 조회가 실패해도 빈 값이 정상 응답처럼 찍힌다. 변수로 받아 실패를 확인한다
  svc_out="$(aws ecs describe-services --region "$(region)" --cluster "$cluster" --services "$svc" \
    --query 'services[0].[status,desiredCount,runningCount,pendingCount]' --output text | tr -d '\r')" \
    || die "서비스 상태를 조회하지 못했습니다(자격 증명이나 서비스 이름을 확인하세요)"
  read -r st dc rc pc <<< "$svc_out"
  echo "서비스: 상태 $st, 원하는 개수 $dc, 실행 중 $rc, 대기 $pc"
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
    -e 's#(([Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]|[Pp][Aa][Ss][Ss][Ww][Dd]|[Pp][Ww][Dd]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Tt][Oo][Kk][Ee][Nn]|[Aa][Pp][Ii][_-]?[Kk][Ee][Yy])[=:" ]+)[^ ",;&]+#\1***#g'
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
    --query 'services[0].{status:status,desired:desiredCount,running:runningCount,pending:pendingCount,deployments:deployments[].{status:status,rolloutState:rolloutState,rolloutStateReason:rolloutStateReason,failedTasks:failedTasks,taskDefinition:taskDefinition},events:events[:8].message}' \
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

  awsn logs tail "$lg" --region "$rgn" --since 30m --format short 2>/dev/null | tail -n 60 | mask > "$tmp/logs.txt" || true

  # 로그뿐 아니라 JSON 전체를 셸 mask()로 거른다(마스킹 규칙을 한 곳에 둔다)
  ID="$id" IMAGE="$(tf "$d" output -raw image)" URL="$(tf "$d" output -raw url)" DIR="$tmp" HIST="$(tail -n 3 "$d/history.log" 2>/dev/null || true)" ATT="$(tail -n 5 "$d/attempts.log" 2>/dev/null || true)" \
  "$PY" - <<'PYEOF' | mask
import json, os, re, sys
d = os.environ["DIR"]
def load(n):
    with open(os.path.join(d, n), encoding="utf-8") as f:
        return json.load(f)
with open(os.path.join(d, "logs.txt"), encoding="utf-8", errors="replace") as f:
    logs = f.read().splitlines()
out = {
    "deployId": os.environ["ID"],
    "image": os.environ["IMAGE"],
    "url": os.environ["URL"],
    "lastHealthyImages": [l.split("|")[1] for l in os.environ["HIST"].splitlines() if "|" in l],
    "recentAttempts": [dict(zip(("time", "result", "image", "reason"), l.split("|", 3))) for l in os.environ.get("ATT", "").splitlines() if "|" in l],
    "service": load("service.json"),
    "stoppedTasks": load("stopped.json"),
    "targets": load("targets.json"),
    "logTail": logs,
}
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
PYEOF
  rm -rf "$tmp"
}

cmd_destroy() {
  local id="${1:-}"; [ -n "$id" ] || die "사용법: destroy <id> [--yes] [--drop-db]"
  shift || true
  local drop=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes) ASSUME_YES=1; shift ;;
      --drop-db) drop=1; shift ;;
      *) die "알 수 없는 옵션: $1" ;;
    esac
  done
  need terraform
  local d; d="$(existing_dir "$id")"
  confirm "배포 $id 를 삭제할까요? (foundation은 지우지 않습니다. 앱 전용 DB는 $([ "$drop" = 1 ] && echo '함께 지웁니다' || echo '보존합니다'))"
  tf "$d" destroy -input=false -auto-approve -no-color | tail -n 10
  if [ -f "$d/db-isolated" ]; then
    if [ "$drop" = "1" ]; then
      export_foundation
      db_drop "$id"
    else
      log "앱 전용 DB $(cat "$d/db-isolated") 와 접속 정보는 남겼습니다. 지우려면: deploy.sh drop-db $id"
    fi
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ > "$d/destroyed"
  log "삭제했습니다. 폴더(state 포함)는 남겨 둡니다: $d (같은 ID로 다시 만들면 deployments/_destroyed로 옮겨집니다)"
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
    build) cmd_build "$@" ;;
    make-id) make_id "${1:-}" ;;
    detect-arch) detect_arch ;;
    image-ref) cmd_image_ref "$@" ;;
    foundation-info) cmd_foundation_info ;;
    foundation-state) cmd_foundation_state ;;
    db-check) export_foundation; cmd_db_check "$@" ;;
    drop-db) export_foundation; cmd_drop_db "$@" ;;
    destroy) cmd_destroy "$@" ;;
    *) sed -n '2,28p' "${BASH_SOURCE[0]}"; exit 1 ;;
  esac
}
# 직접 실행할 때만 main을 돌린다. test_infra.py가 함수만 시험하려고 source로 불러올 때는 돌리지 않는다
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
