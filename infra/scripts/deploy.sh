#!/usr/bin/env bash
# 배포 1건을 만들고(up), 이미지·설정을 바꾸고(update), 되돌리고(rollback), 상태를 보고(status),
# 실패 원인을 모으고(diagnose), 지운다(destroy).
# infra/README.md의 "배포 1건" 절차를 그대로 실행한다. 사람이 하던 명령을 순서대로 묶은 것이다.
#
# 사용법
#   deploy.sh up       --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --app app.json [--port 8001|auto] [--yes]
#                      [--plan-only] [--shared-db] [--grace 초] [--skip-nat-check] [--arch ARM64]
#   deploy.sh update   a1b2c3d4 [--image <ecr_url>:tag] [--app app.json] [--yes] [--plan-only]
#   deploy.sh rollback a1b2c3d4 [--to <이미지>] [--yes] [--plan-only]
#   deploy.sh apply    a1b2c3d4            # 위 명령을 --plan-only 로 만든, 승인된 저장 계획만 적용한다
#   deploy.sh status   a1b2c3d4
#   deploy.sh diagnose a1b2c3d4            # 실패 분석용 JSON(비밀 마스킹됨)을 표준 출력으로 낸다
#   deploy.sh db-check a1b2c3d4            # 앱 전용 DB 계정이 자기 DB만 쓸 수 있는지 확인한다
#   deploy.sh destroy  a1b2c3d4 [--yes] [--drop-db]
#   deploy.sh drop-db  a1b2c3d4 [--yes]    # 앱 전용 DB와 계정을 지운다 (되돌릴 수 없다)
#   deploy.sh foundation-state             # foundation state를 S3로 옮긴다 (PAVED_STATE_BUCKET 필요)
#
# 필요한 것: terraform(>=1.6), aws CLI(자격증명 설정 완료), python(python3 또는 python),
#            앱별 DB를 쓰려면 docker(mysql 클라이언트 이미지를 ECR에 올릴 때 최초 1회).
# foundation은 먼저 apply되어 있어야 한다.
# 환경 변수
#   AWS_REGION          리전. 없으면 aws configure의 리전
#   HEALTH_TIMEOUT      헬스체크 대기 시간(초, 기본 300)
#   PAVED_STATE_BUCKET  지정하면 배포 state를 이 S3 버킷에 저장한다(infra/bootstrap으로 만든다). 없으면 로컬 파일
#
# 설계 규칙 (AGENTS.md 6장)
#   - apply는 항상 승인된 저장 plan(tfplan)만 실행한다. 승인 후 새 plan을 만들지 않는다.
#   - 롤백은 사람이 명령으로 실행한다. 자동 복구는 범위가 확정되기 전이라 만들지 않았다.
#   - 이력(history.log)에는 헬스체크를 통과한 이미지만 남긴다.
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
# 공유 ALB에서 이미 쓰는 리스너 포트를 조회해서 허용 범위의 가장 작은 빈 포트를 고른다.
# 동시에 두 배포를 만들면 같은 포트를 고를 수 있다. 그때는 늦게 apply한 쪽이 실패한다.
pick_port() {
  local rgn alb from to used p PY
  rgn="$(region)"; alb="$(fjson alb_arn)"; PY="$(pick_python)"
  from="$(fjson allowed_listener_ports from)"; to="$(fjson allowed_listener_ports to)"
  used="$(aws elbv2 describe-listeners --region "$rgn" --load-balancer-arn "$alb" --query 'Listeners[].Port' --output text 2>/dev/null || true)"
  "$PY" - "$from" "$to" "$used" <<'PYEOF'
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

# --- state 위치 ----------------------------------------------------------------------------------
# PAVED_STATE_BUCKET이 있으면 배포 state를 S3에 둔다. 로컬 파일은 잃어버리면 AWS 리소스를 지울 수 없고,
# DB 비밀번호가 평문으로 들어 있다. S3는 버전 관리·암호화·잠금(use_lockfile)을 쓴다.
# 템플릿의 backend "local"을 override 파일로 바꾼다.
state_setup() {
  local d="$1" id="$2"
  [ -n "${PAVED_STATE_BUCKET:-}" ] || return 0
  cat > "$d/backend_override.tf" <<EOF
terraform {
  backend "s3" {
    bucket       = "${PAVED_STATE_BUCKET}"
    key          = "deployments/${id}/terraform.tfstate"
    region       = "${PAVED_STATE_REGION:-$(region)}"
    encrypt      = true
    use_lockfile = true
  }
}
EOF
  log "state를 S3에 저장합니다: s3://${PAVED_STATE_BUCKET}/deployments/${id}/terraform.tfstate"
}

# foundation의 state도 S3로 옮긴다. foundation은 DB 비밀번호가 든 state를 가장 많이 담고 있어서 우선순위가 높다.
# 로컬 state가 있으면 그대로 복사하고, 없으면 새로 시작한다. 환경마다 값이 다른 backend_override.tf는 Git에서 제외된다
cmd_foundation_state() {
  [ -n "${PAVED_STATE_BUCKET:-}" ] || die "PAVED_STATE_BUCKET을 지정하세요 (infra/bootstrap으로 만든 버킷)"
  need terraform
  cat > "$FOUNDATION/backend_override.tf" <<EOF
terraform {
  backend "s3" {
    bucket       = "${PAVED_STATE_BUCKET}"
    key          = "foundation/terraform.tfstate"
    region       = "${PAVED_STATE_REGION:-$(region)}"
    encrypt      = true
    use_lockfile = true
  }
}
EOF
  tf "$FOUNDATION" init -input=false -migrate-state -force-copy >/dev/null
  log "foundation state를 S3로 옮겼습니다: s3://${PAVED_STATE_BUCKET}/foundation/terraform.tfstate"
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
  (
    export DOCKER_CONFIG; DOCKER_CONFIG="$(native_path "$cfg")"
    aws ecr get-login-password --region "$rgn" | docker login --username AWS --password-stdin "$registry" >/dev/null
    docker pull --platform linux/amd64 public.ecr.aws/docker/library/mysql:8.4 >/dev/null
    docker tag public.ecr.aws/docker/library/mysql:8.4 "$ecr:$TOOLS_TAG"
    docker push "$ecr:$TOOLS_TAG" >/dev/null
  ) || { rm -rf "${cfg:?}"; die "mysql 클라이언트 이미지를 올리지 못했습니다"; }
  rm -rf "${cfg:?}"
}

# 1회성 DB 작업의 작업 정의 JSON을 만든다. mode: provision | drop | verify
db_taskdef_json() {
  local mode="$1" id="$2" PY; PY="$(pick_python)"
  # 값은 먼저 모두 읽어 둔다. 같은 줄에서 MSYS_NO_PATHCONV=1을 앞에 두면 뒤쪽 $(fjson ...)에도 적용되어
  # Python이 /c/... 경로를 열지 못한다
  local family="paved-clouds-dbinit-$id-$mode" rgn ecr exec_role lg host port admin_user admin_arn app_arn app_db
  rgn="$(region)"; ecr="$(fjson ecr_repository_url)"; exec_role="$(fjson execution_role_arn)"
  lg="$(fjson db_provisioner_log_group)"; host="$(fjson db_host)"; port="$(fjson db_port)"
  admin_user="$(fjson db_admin_username)"; admin_arn="$(fjson db_admin_password_parameter_arn)"
  app_arn="$(db_param_arn "$id")"; app_db="$(db_name_of "$id")"
  # Git Bash는 /로 시작하는 환경 변수 값(로그 그룹 이름 등)을 Windows 경로로 바꾼다. 이 변환을 끄지 않으면
  # awslogs-group이 C:/Program Files/Git/... 로 들어가 작업 정의 등록이 실패한다. 변환 끄기는 이 호출에만 적용한다
  MSYS_NO_PATHCONV=1 MODE="$mode" ID="$id" FAMILY="$family" RGN="$rgn" ECR="$ecr" EXEC="$exec_role" LG="$lg" \
  DB_HOST="$host" DB_PORT="$port" DB_ADMIN_USER="$admin_user" ADMIN_PW_ARN="$admin_arn" APP_PARAM_ARN="$app_arn" \
  APP_DB="$app_db" TOOLS_TAG="$TOOLS_TAG" \
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
    {"name": "OTHER_DB", "value": "app"},
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

# 1회성 DB 작업을 실행하고 종료 코드를 확인한다. 로그(비밀 마스킹)는 표준 오류로 보여 준다
db_task() {
  local mode="$1" id="$2" rgn cluster td_arn task_arn code subnets sg pub tid events
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
  tid="${task_arn##*/}"
  events="$(awsn logs get-log-events --region "$rgn" --log-group-name "$(fjson db_provisioner_log_group)" \
    --log-stream-name "dbinit/dbinit/$tid" --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | mask || true)"
  [ -n "$events" ] && printf '%s\n' "$events" | sed 's/^/    | /' >&2
  awsn ecs deregister-task-definition --region "$rgn" --task-definition "$td_arn" >/dev/null 2>&1 || true
  [ "$code" = "0" ] || die "DB 작업($mode)이 실패했습니다 (종료 코드 ${code:-알 수 없음}). 위 로그를 확인하세요"
  DB_TASK_LOG="$events"
}

random_password() {
  "$(pick_python)" -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
}

# 앱 전용 DB·계정을 만들고 접속 정보를 SSM에 저장한다. apply 직전(승인 후)에만 실행한다
db_provision() {
  local id="$1" d name pw host port url rgn
  d="$(deploy_dir "$id")"; name="$(db_name_of "$id")"; rgn="$(region)"
  host="$(fjson db_host)"; port="$(fjson db_port)"; pw="$(random_password)"
  url="mysql://${name}:${pw}@${host}:${port}/${name}"
  log "앱 전용 DB 준비: $name"
  awsn ssm put-parameter --region "$rgn" --name "$(db_param_name "$id")" --type SecureString --overwrite \
    --description "Per-app DATABASE_URL for deployment $id" --value "$url" >/dev/null || die "접속 정보를 SSM에 저장하지 못했습니다"
  awsn ssm add-tags-to-resource --region "$rgn" --resource-type Parameter --resource-id "$(db_param_name "$id")" \
    --tags Key=Project,Value=paved-clouds Key=DeployId,Value="$id" Key=ManagedBy,Value=paved-clouds-platform >/dev/null 2>&1 || true
  unset pw url
  db_task provision "$id"
  : > "$d/db-provisioned"
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
  # 앱 전용 DB는 승인된 뒤에만 만든다 (계획 단계에서는 접속 정보 ARN만 쓰고 아무것도 만들지 않는다)
  if [ -f "$d/db-isolated" ] && [ ! -f "$d/db-provisioned" ]; then
    export_foundation
    db_provision "$id"
  fi
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
  local id="" image="" port="auto" app="" grace="" shared_db=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="$2"; shift 2 ;;
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
  [ -n "$id" ] && [ -n "$image" ] && [ -n "$app" ] || die "--id --image --app 이 모두 필요합니다"
  valid_id "$id"
  [ "$port" = "auto" ] || [[ "$port" =~ ^[0-9]+$ ]] || die "--port는 숫자 또는 auto여야 합니다"
  [ -z "$grace" ] || [[ "$grace" =~ ^[0-9]+$ ]] || die "--grace는 숫자(초)여야 합니다"
  [ -f "$app" ] || die "app 파일이 없습니다: $app"
  need terraform; need aws

  local d; d="$(deploy_dir "$id")"
  [ ! -e "$d" ] || die "이미 있는 배포입니다: $d (새 ID를 쓰거나 destroy 후 다시 하세요)"

  local rgn; rgn="$(region)"
  log "배포 $id 준비 (리전 $rgn)"

  export_foundation
  nat_preflight

  if [ "$port" = "auto" ]; then
    port="$(pick_port)" || die "허용 범위에 빈 포트가 없습니다"
    log "리스너 포트 자동 할당: $port"
  fi

  # 앱이 DB를 쓰면 기본으로 앱 전용 DB를 쓴다. --shared-db 이면 공유 DB(관리자 계정)를 쓴다
  local use_db param_arn=""
  use_db="$("$(pick_python)" -c "import json,sys;print(str(bool(json.load(open(sys.argv[1],encoding='utf-8')).get('use_database'))).lower())" "$app")"
  if [ "$use_db" = "true" ] && [ "$shared_db" = "0" ]; then
    param_arn="$(db_param_arn "$id")"
    log "앱 전용 DB를 사용합니다: $(db_name_of "$id") (접속 정보는 승인 후 apply 때 만듭니다)"
  elif [ "$use_db" = "true" ]; then
    log "경고: 공유 DB를 DB 관리자 계정으로 사용합니다(--shared-db)"
  fi

  # provider를 배포마다 다시 받지 않도록 캐시를 쓴다
  export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
  mkdir -p "$TF_PLUGIN_CACHE_DIR"

  mkdir -p "$d"
  tar -C "$TEMPLATE" --exclude=.terraform -cf - . | tar -C "$d" -xf -
  [ -z "$param_arn" ] || printf '%s\n' "$(db_name_of "$id")" > "$d/db-isolated"

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
#   - ECS 배포가 COMPLETED이고 대상이 healthy일 때만 통과한다. 대상만 보면 롤링 교체 중 옛 태스크 때문에 일찍 통과한다
#   - 앱이 응답은 하는데 헬스체크 코드가 계속 틀리면(예: 경로 오류 404) 기다려도 소용없으니 실패로 확정한다(실측 apply 후 약 3분)
#   - 태스크가 반복해서 죽거나 시간이 지나도 실패로 끝낸다. ECS 서킷 브레이커가 배포를 FAILED로 만드는 데는 더 걸린다
wait_healthy() {
  local id="$1" d tg cluster svc start state stopped rollout reasons mismatch=0 rgn
  d="$(deploy_dir "$id")"; rgn="$(region)"
  tg="$(tf "$d" output -raw target_group_arn)"
  cluster="$(tf "$d" output -raw cluster_name)"
  svc="$(tf "$d" output -raw service_name)"
  start=$(date +%s)
  log "헬스체크 대기 (최대 ${HEALTH_TIMEOUT}초)"
  while true; do
    state="$(aws elbv2 describe-target-health --region "$rgn" --target-group-arn "$tg" \
      --query 'TargetHealthDescriptions[].TargetHealth.State' --output text 2>/dev/null || true)"
    # 헬스체크 실패 설명(예: "Health checks failed with these codes: [404]")
    reasons="$(aws elbv2 describe-target-health --region "$rgn" --target-group-arn "$tg" \
      --query 'TargetHealthDescriptions[].TargetHealth.Description' --output text 2>/dev/null || true)"
    rollout="$(aws ecs describe-services --region "$rgn" --cluster "$cluster" --services "$svc" \
      --query 'services[0].deployments[?status==`PRIMARY`]|[0].rolloutState' --output text 2>/dev/null || true)"
    log "대상=${state:-등록 대기} 배포=${rollout:-?}"
    if [ "$rollout" = "FAILED" ]; then
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
      log "앱이 응답하지만 헬스체크가 4xx 코드를 돌려줍니다(${mismatch}회 연속). health_check_path가 틀렸을 가능성이 큽니다"
      log "ECS 서킷 브레이커가 배포를 FAILED로 확정하기까지는 더 걸리고, 그동안 태스크 교체를 반복합니다"
      return 1
    fi
    # 태스크가 계속 뜨자마자 죽으면 시간 초과를 기다리지 않고 바로 실패로 본다
    stopped="$(aws ecs list-tasks --region "$rgn" --cluster "$cluster" --service-name "$svc" \
      --desired-status STOPPED --query 'length(taskArns)' --output text 2>/dev/null || echo 0)"
    if [ "${stopped:-0}" -ge 3 ]; then
      log "태스크가 반복해서 종료되었습니다(최근 ${stopped}개)"
      return 1
    fi
    if [ $(( $(date +%s) - start )) -ge "$HEALTH_TIMEOUT" ]; then
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
  if [ -f "$d/db-isolated" ]; then echo "DB: 앱 전용 $(cat "$d/db-isolated")"; else echo "DB: 공유 또는 미사용"; fi
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

  ID="$id" IMAGE="$(tf "$d" output -raw image)" URL="$(tf "$d" output -raw url)" DIR="$tmp" HIST="$(tail -n 3 "$d/history.log" 2>/dev/null || true)" \
  "$PY" - <<'PYEOF'
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
    foundation-state) cmd_foundation_state ;;
    db-check) export_foundation; cmd_db_check "$@" ;;
    drop-db) export_foundation; cmd_drop_db "$@" ;;
    destroy) cmd_destroy "$@" ;;
    *) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 1 ;;
  esac
}
# 직접 실행할 때만 main을 돌린다. test_infra.py가 함수만 시험하려고 source로 불러올 때는 돌리지 않는다
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
