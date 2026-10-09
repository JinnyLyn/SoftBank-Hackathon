#!/usr/bin/env python3
"""infra 회귀 시험. AWS에 리소스를 만들지 않는다 (terraform plan과 스크립트 함수만 쓴다).

실행: python infra/scripts/test_infra.py [--skip-aws]

확인하는 것
  A. 배포 모듈 입력 검증: 잘못된 값이 Terraform 단계에서 막히는지, 정상 값은 통과하는지
  B. 배포 모듈 계획 내용: 앱 전용 보안 그룹, 서킷 브레이커, HTTPS 리스너, 앱별 DB 접속 정보
  C. foundation 변수 검증과 계획 내용(NAT 수, AZ 수, AMI 고정, HTTPS, RDS 보호 옵션)
     C의 계획 내용은 AWS 조회(가용 영역, AMI 파라미터)가 필요해서 자격증명이 없으면 건너뛴다
  D. deploy.sh 함수: deploy_id 규칙, 앱 전용 DB 이름·ARN, 비밀 마스킹, DB 작업 정의(비밀번호가 들어가지 않는지), state 설정
  E. 정적 검사: terraform fmt, validate (foundation, 템플릿, 부트스트랩)

시험 결과 판정은 Terraform이 실제로 내는 메시지로만 한다. 시험 환경이 고장난 것(초기화 실패 등)은
"차단"이 아니라 "시험환경오류"로 따로 센다. 이 구분이 없으면 환경 오류가 전부 "차단 성공"으로 읽힌다.

시험용 폴더는 infra/deployments/_test*, (Git 제외 대상 infra/deployments/*)에 만들고 끝나면 지운다.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
WORK = INFRA / "deployments" / "_test"            # foundation 복사본 등 (경로 깊이와 무관한 것)
MOD_DIR = INFRA / "deployments" / "_test_module"  # 템플릿 복사본. ../../modules 가 맞도록 deployments 바로 아래
VAL_DIR = INFRA / "deployments" / "_test_validate"
SKIP_AWS = "--skip-aws" in sys.argv
# 시험용 복사에서 제외할 것. backend_override.tf(담당자 PC의 S3 state 설정)가 섞이면 Terraform이 백엔드 초기화를 요구하며 멈춘다
COPY_IGNORE = shutil.ignore_patterns(".terraform", "*.tfstate*", "*.tfplan", "*.tfvars.json", "backend_override.tf")

FAILS = []
COUNTS = {"ok": 0, "fail": 0, "env": 0, "skip": 0}
VALIDATION_MARKERS = ("Invalid value for variable", "Resource precondition failed", "No value for required variable")


def say(status, name, detail=""):
    mark = {"ok": "OK  ", "fail": "FAIL", "env": "ENV!", "skip": "SKIP"}[status]
    COUNTS[status] += 1
    if status in ("fail", "env"):
        FAILS.append(name)
    print(f"{mark} {name}" + (f"  [{detail}]" if detail else ""))


def tf(cwd, *args, extra_env=None):
    env = dict(os.environ)
    env["TF_IN_AUTOMATION"] = "1"
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["terraform", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def classify(code, out):
    """통과 | 차단 | 시험환경오류"""
    if code == 0 and "Plan:" in out:
        return "통과"
    if any(m in out for m in VALIDATION_MARKERS):
        return "차단"
    return "시험환경오류"


def expect(name, want, code, out):
    got = classify(code, out)
    if got == "시험환경오류":
        say("env", name, out.strip().replace("\n", " ")[:160])
    elif got == want:
        say("ok", name)
    else:
        say("fail", f"{name} (기대 {want}, 실제 {got})", out.strip().replace("\n", " ")[:120])


def find_bash():
    """실제로 스크립트를 실행할 수 있는 bash. Windows에서 PATH의 bash는 WSL 중계 실행 파일일 수 있어 직접 확인한다"""
    cands = [shutil.which("bash")]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Git", "bin", "bash.exe"), os.path.join(base, "Programs", "Git", "bin", "bash.exe")]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        try:
            r = subprocess.run([c, "-c", "echo $((40+2))"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip() == "42":
                return c
        except Exception:
            continue
    return None


BASH = None


def aws_ready():
    if SKIP_AWS:
        return False
    try:
        r = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def plan_json(cwd, *args):
    code, out = tf(cwd, "plan", "-input=false", "-refresh=false", "-lock=false", "-no-color", "-out=t.plan", *args)
    if code != 0:
        return None, out
    code, js = tf(cwd, "show", "-json", "t.plan")
    if code != 0:
        return None, js
    return json.loads(js), out


def planned(plan, address_prefix):
    """계획에서 주소가 prefix로 시작하는 생성 리소스들의 after 값"""
    res = []
    for rc in plan.get("resource_changes", []):
        if rc["address"].startswith(address_prefix) and "create" in rc["change"]["actions"]:
            res.append(rc["change"]["after"])
    return res


# ------------------------------------------------------------------------------------------------
FAKE_FOUNDATION = {
    "alb_arn": "arn:aws:elasticloadbalancing:sa-east-1:123456789012:loadbalancer/app/paved-clouds-alb/0000000000000000",
    "alb_dns_name": "paved-clouds-alb-1.sa-east-1.elb.amazonaws.com",
    "alb_security_group_id": "sg-0000000000000000a",
    "allowed_listener_ports": {"from": 8001, "to": 8049},
    "assign_public_ip": False,
    "certificate_arn": "",
    "cluster_name": "paved-clouds",
    "database_url_parameter_arn": "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/database-url",
    "db_admin_password_parameter_arn": "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/db-admin-password",
    "db_admin_username": "paved_admin",
    "db_host": "paved-clouds-mysql.example.sa-east-1.rds.amazonaws.com",
    "db_port": 3306,
    "db_provisioner_log_group": "/paved-clouds/db-provisioner",
    "db_provisioner_security_group": "sg-0000000000000000c",
    "db_security_group_id": "sg-0000000000000000b",
    "ecr_repository_url": "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps",
    "execution_role_arn": "arn:aws:iam::123456789012:role/paved-clouds-task-execution",
    "listener_protocol": "HTTP",
    "task_subnet_ids": ["subnet-00000000000000001", "subnet-00000000000000002", "subnet-00000000000000003"],
    "vpc_id": "vpc-00000000000000000",
}
FAKE_CERT = "arn:aws:acm:sa-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012"
ECR = FAKE_FOUNDATION["ecr_repository_url"]
BASE_P = {"region": "sa-east-1", "deploy_id": "abcd1234", "image": ECR + ":abcd1234-r1", "cpu_architecture": "X86_64",
          "listener_port": 8001, "foundation": FAKE_FOUNDATION}
BASE_A = {"container_port": 8000, "health_check_path": "/health", "task_size": "xsmall", "min_tasks": 1, "max_tasks": 1,
          "use_database": True, "environment": {"COOKIE_SECURE": "false"}}


def prepare_module_dir():
    d = MOD_DIR
    shutil.copytree(INFRA / "deployments" / "_template", d, ignore=COPY_IGNORE)
    code, out = tf(d, "init", "-input=false")
    if code != 0:
        raise SystemExit("모듈 시험 폴더 init 실패:\n" + out)
    return d


def run_module(d, p, a):
    (d / "platform.auto.tfvars.json").write_text(json.dumps({"platform": p}), encoding="utf-8")
    (d / "app.auto.tfvars.json").write_text(json.dumps({"app": a}), encoding="utf-8")
    return tf(d, "plan", "-input=false", "-refresh=false", "-lock=false", "-no-color")


def case_module(d, name, want, pm=None, am=None):
    p, a = copy.deepcopy(BASE_P), copy.deepcopy(BASE_A)
    if pm:
        pm(p)
    if am:
        am(a)
    code, out = run_module(d, p, a)
    expect("A " + name, want, code, out)
    return code, out


def part_a(d):
    print("\n=== A. 배포 모듈 입력 검증 ===")
    case_module(d, "정상 입력", "통과")
    case_module(d, "정상: 이미지 다이제스트", "통과", lambda p: p.update(image=ECR + "@sha256:" + "a" * 64))
    case_module(d, "정상: 오토스케일링 min1~max3", "통과", am=lambda a: a.update(max_tasks=3))
    case_module(d, "정상: 헬스체크 유예 30초", "통과", lambda p: p.update(health_check_grace_seconds=30))
    for n, k, v in [("container_port=0", "container_port", 0), ("container_port=70000", "container_port", 70000),
                    ("container_port=80.5", "container_port", 80.5), ("health_check_path='health'", "health_check_path", "health"),
                    ("health_check_path='/a b'", "health_check_path", "/a b"), ("task_size='large'", "task_size", "large")]:
        case_module(d, n, "차단", am=lambda a, k=k, v=v: a.update({k: v}))
    case_module(d, "min_tasks=3", "차단", am=lambda a: a.update(min_tasks=3, max_tasks=3))
    case_module(d, "max_tasks=5", "차단", am=lambda a: a.update(max_tasks=5))
    case_module(d, "min_tasks=2,max_tasks=1", "차단", am=lambda a: a.update(min_tasks=2, max_tasks=1))
    for k in ["DATABASE_URL", "DB_PASSWORD", "API_KEY", "lower"]:
        case_module(d, f"environment에 {k}", "차단", am=lambda a, k=k: a["environment"].update({k: "x"}))
    case_module(d, "environment 21개", "차단", am=lambda a: a.update(environment={f"K{i}": "v" for i in range(21)}))
    case_module(d, "image :latest", "차단", lambda p: p.update(image=ECR + ":latest"))
    case_module(d, "image 태그 없음", "차단", lambda p: p.update(image=ECR))
    case_module(d, "image docker.io", "차단", lambda p: p.update(image="docker.io/library/nginx:1"))
    case_module(d, "image 다른 ECR 저장소", "차단", lambda p: p.update(image="111122223333.dkr.ecr.sa-east-1.amazonaws.com/other/x:1"))
    for n, v in [("listener_port=9000", 9000), ("listener_port=80", 80), ("listener_port=8001.5", 8001.5)]:
        case_module(d, n, "차단", lambda p, v=v: p.update(listener_port=v))
    case_module(d, "deploy_id='ab'", "차단", lambda p: p.update(deploy_id="ab", image=ECR + ":ab-r1"))
    case_module(d, "deploy_id='abc-1234'(하이픈)", "차단", lambda p: p.update(deploy_id="abc-1234"))
    case_module(d, "deploy_id 대문자", "차단", lambda p: p.update(deploy_id="ABCD1234"))
    case_module(d, "cpu_architecture='arm'", "차단", lambda p: p.update(cpu_architecture="arm"))
    case_module(d, "use_database=true, 파라미터 ARN 빈값", "차단", lambda p: p["foundation"].update(database_url_parameter_arn=""))
    case_module(d, "health_check_grace_seconds=601", "차단", lambda p: p.update(health_check_grace_seconds=601))
    case_module(d, "database_url_parameter_arn 형식 오류", "차단", lambda p: p.update(database_url_parameter_arn="not-an-arn"))
    case_module(d, "listener_protocol='FTP'", "차단", lambda p: p["foundation"].update(listener_protocol="FTP"))
    case_module(d, "HTTPS인데 certificate_arn 비어 있음", "차단", lambda p: p["foundation"].update(listener_protocol="HTTPS"))
    case_module(d, "정상: HTTPS + 인증서", "통과", lambda p: p["foundation"].update(listener_protocol="HTTPS", certificate_arn=FAKE_CERT))


def part_b(d):
    print("\n=== B. 배포 모듈 계획 내용 ===")

    # 예시 파일이 낡으면(없어진 필드, 빠진 필수 필드) 복사해서 쓰는 사람이 plan 단계에서 막힌다
    shutil.copy(d / "platform.auto.tfvars.json.example", d / "platform.auto.tfvars.json")
    shutil.copy(d / "app.auto.tfvars.json.example", d / "app.auto.tfvars.json")
    code, out = tf(d, "plan", "-input=false", "-refresh=false", "-lock=false", "-no-color")
    expect("B 예시 입력 파일(.example)이 현재 계약으로 plan을 통과한다", "통과", code, out)

    def planned_with(pm=None, am=None):
        p, a = copy.deepcopy(BASE_P), copy.deepcopy(BASE_A)
        if pm:
            pm(p)
        if am:
            am(a)
        (d / "platform.auto.tfvars.json").write_text(json.dumps({"platform": p}), encoding="utf-8")
        (d / "app.auto.tfvars.json").write_text(json.dumps({"app": a}), encoding="utf-8")
        return plan_json(d)

    plan, out = planned_with()
    if plan is None:
        say("env", "B 기준 계획 만들기", out[:150].replace("\n", " "))
        return

    def is_(name, cond, detail=""):
        say("ok" if cond else "fail", "B " + name, "" if cond else detail)

    svc = planned(plan, "module.app.aws_ecs_service.app")
    is_("ECS 서비스가 계획에 있다", len(svc) == 1)
    if svc:
        cb = (svc[0].get("deployment_circuit_breaker") or [{}])[0]
        is_("서킷 브레이커 enable=true, rollback=false", cb.get("enable") is True and cb.get("rollback") is False, str(cb))
        is_("헬스체크 유예 기본 90초", svc[0].get("health_check_grace_period_seconds") == 90, str(svc[0].get("health_check_grace_period_seconds")))
    ingress = planned(plan, "module.app.aws_vpc_security_group_ingress_rule.task_from_alb")
    is_("태스크 보안 그룹은 컨테이너 포트 하나만 받는다", len(ingress) == 1 and ingress[0]["from_port"] == 8000 and ingress[0]["to_port"] == 8000, str(ingress))
    egress = planned(plan, "module.app.aws_vpc_security_group_egress_rule.alb_to_task")
    is_("ALB는 그 앱의 컨테이너 포트로만 나간다", len(egress) == 1 and egress[0]["from_port"] == 8000, str(egress))
    is_("DB를 쓰면 DB 보안 그룹에 3306 규칙이 생긴다", len(planned(plan, "module.app.aws_vpc_security_group_ingress_rule.db_from_task")) == 1)
    td = planned(plan, "module.app.aws_ecs_task_definition.app")
    if td:
        cd = json.loads(td[0]["container_definitions"])[0]
        sec = {s["name"]: s["valueFrom"] for s in cd.get("secrets", [])}
        is_("DB를 쓰면 DATABASE_URL을 secrets로 주입(공유 파라미터)", sec.get("DATABASE_URL") == FAKE_FOUNDATION["database_url_parameter_arn"], str(sec))
        is_("평문 환경 변수에 DATABASE_URL이 없다", all(e["name"] != "DATABASE_URL" for e in cd["environment"]))

    app_arn = "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/abcd1234/database-url"
    plan, out = planned_with(pm=lambda p: p.update(database_url_parameter_arn=app_arn))
    if plan:
        cd = json.loads(planned(plan, "module.app.aws_ecs_task_definition.app")[0]["container_definitions"])[0]
        sec = {s["name"]: s["valueFrom"] for s in cd.get("secrets", [])}
        is_("앱 전용 파라미터가 있으면 공유 파라미터 대신 쓴다", sec.get("DATABASE_URL") == app_arn, str(sec))
    plan, out = planned_with(am=lambda a: a.update(use_database=False))
    if plan:
        is_("DB를 안 쓰면 DB 보안 그룹 규칙이 없다", len(planned(plan, "module.app.aws_vpc_security_group_ingress_rule.db_from_task")) == 0)
        td = planned(plan, "module.app.aws_ecs_task_definition.app")
        cd = json.loads(td[0]["container_definitions"])[0]
        is_("DB를 안 쓰면 secrets가 비어 있다", not cd.get("secrets"))
    plan, out = planned_with(pm=lambda p: p.update(health_check_grace_seconds=30))
    if plan:
        svc = planned(plan, "module.app.aws_ecs_service.app")
        is_("헬스체크 유예를 30초로 바꾸면 반영된다", svc and svc[0].get("health_check_grace_period_seconds") == 30)
    plan, out = planned_with(pm=lambda p: p["foundation"].update(listener_protocol="HTTPS", certificate_arn=FAKE_CERT))
    if plan:
        lst = planned(plan, "module.app.aws_lb_listener.app")
        is_("HTTPS면 리스너에 인증서와 TLS 정책이 붙는다", lst and lst[0]["protocol"] == "HTTPS" and lst[0]["certificate_arn"] == FAKE_CERT and lst[0]["ssl_policy"], str(lst[:1]))
        is_("HTTPS면 접속 URL이 https://", "https://" in out, "")


def prepare_foundation_dir():
    d = WORK / "foundation"
    shutil.copytree(INFRA / "foundation", d, ignore=COPY_IGNORE)
    code, out = tf(d, "init", "-input=false", "-backend=false")
    if code != 0:
        raise SystemExit("foundation 시험 폴더 init 실패:\n" + out)
    return d


def part_c(d, with_aws):
    print("\n=== C. foundation 변수 검증 ===")

    def inv(name, *args, want="차단"):
        code, out = tf(d, "plan", "-input=false", "-refresh=false", "-lock=false", "-no-color", *args)
        # 변수 검증은 AWS 조회 전에 끝나므로 자격증명 없이도 판정된다. 통과를 기대하는 항목은 아래 계획 시험에서 본다
        if want == "차단":
            if any(m in out for m in VALIDATION_MARKERS):
                say("ok", "C " + name)
            else:
                say("fail" if code == 0 else "env", "C " + name + " (차단되지 않음)", out.strip().replace("\n", " ")[:140])

    R = "-var=region=sa-east-1"
    inv("region 생략")
    inv("region='korea'", "-var=region=korea")
    inv("project='Ab'", R, "-var=project=Ab")
    inv("project='ab'(짧음)", R, "-var=project=ab")
    inv("vpc_cidr=/24", R, "-var=vpc_cidr=10.0.0.0/24")
    inv("db_engine_version=8.0", R, "-var=db_engine_version=8.0")
    inv("db_engine_version=8.0.35", R, "-var=db_engine_version=8.0.35")
    inv("nat_instance_type=t4g.nano", R, "-var=nat_instance_type=t4g.nano")
    inv("nat_instance_type=t4g.micro", R, "-var=nat_instance_type=t4g.micro")
    inv("nat_instance_type=t3.small(x86)", R, "-var=nat_instance_type=t3.small")
    inv("listener_port_range 50개", R, "-var=listener_port_range={from=8001,to=8060}")
    inv("listener_port_range 1024 미만", R, "-var=listener_port_range={from=80,to=100}")
    inv("az_count=1", R, "-var=az_count=1")
    inv("az_count=4", R, "-var=az_count=4")
    inv("certificate_arn 형식 오류", R, "-var=certificate_arn=nope")
    inv("nat_ami_id 형식 오류", R, "-var=nat_ami_id=ubuntu")
    inv("db_backup_retention_days=0", R, "-var=db_backup_retention_days=0")
    inv("db_backup_retention_days=36", R, "-var=db_backup_retention_days=36")

    if not with_aws:
        print("\n=== C. foundation 계획 내용 === (AWS 자격증명이 없거나 --skip-aws라서 건너뜀)")
        COUNTS["skip"] += 1
        return
    print("\n=== C. foundation 계획 내용 (AWS 조회 필요, 리소스는 만들지 않음) ===")

    def pj(*args):
        return plan_json(d, "-var=region=sa-east-1", *args)

    def is_(name, cond, detail=""):
        say("ok" if cond else "fail", "C " + name, "" if cond else detail)

    base, out = pj("-var=enable_nat_instance=true")
    if base is None:
        say("env", "C 기준 계획 만들기", out[:200].replace("\n", " "))
        return
    n = lambda plan, prefix: len(planned(plan, prefix))
    is_("기본: NAT 3대, 서브넷 6개", n(base, "aws_instance.nat") == 3 and n(base, "aws_subnet.") == 6, f"nat={n(base,'aws_instance.nat')} subnet={n(base,'aws_subnet.')}")
    is_("기본: NAT 타입 t4g.small", all(x["instance_type"] == "t4g.small" for x in planned(base, "aws_instance.nat")))
    is_("기본: 앱 공용 태스크 보안 그룹이 없다", n(base, "aws_security_group.tasks") == 0)
    is_("기본: ALB 보안 그룹에 앱 포트로 나가는 규칙이 없다(배포 모듈이 추가)", n(base, "aws_vpc_security_group_egress_rule.alb") == 0)
    is_("기본: HTTPS 리스너 없음, 80번은 404 고정 응답", n(base, "aws_lb_listener.https_default") == 0
        and planned(base, "aws_lb_listener.default")[0]["default_action"][0]["type"] == "fixed-response")
    is_("기본: RDS 백업 7일, 최종 스냅샷 남김", planned(base, "aws_db_instance.this")[0]["backup_retention_period"] == 7
        and planned(base, "aws_db_instance.this")[0]["skip_final_snapshot"] is False)
    is_("기본: RDS 삭제 보호 켜짐, 단일 AZ", planned(base, "aws_db_instance.this")[0]["deletion_protection"] is True
        and planned(base, "aws_db_instance.this")[0]["multi_az"] is False)
    # IAM 정책은 다른 리소스의 ARN이 apply 때 정해져서 계획에서는 값을 볼 수 없다. 정책 코드에 경로가 있는지 확인한다
    is_("기본: IAM이 앱별 DB 접속 정보 경로(/apps/*)를 읽을 수 있다", "/apps/*" in (INFRA / "foundation" / "iam.tf").read_text(encoding="utf-8"))

    ha, out = pj("-var=enable_nat_instance=true", "-var=nat_high_availability=false")
    if ha:
        is_("nat_high_availability=false: NAT 1대, 라우트는 3개 모두 그 NAT로", n(ha, "aws_instance.nat") == 1 and n(ha, "aws_route.private_nat") == 3
            and n(ha, "aws_eip.nat") == 1 and n(ha, "aws_cloudwatch_metric_alarm.nat_recover") == 1)
    az2, out = pj("-var=enable_nat_instance=true", "-var=az_count=2")
    if az2:
        is_("az_count=2: 서브넷 4개, NAT 2대", n(az2, "aws_subnet.") == 4 and n(az2, "aws_instance.nat") == 2)
    nonat, out = pj("-var=enable_nat_instance=false")
    if nonat:
        is_("NAT 끔: NAT 인스턴스 0", n(nonat, "aws_instance.nat") == 0)
        di = nonat["output_changes"]["deploy_inputs"]["after"]
        is_("NAT 끔: 앱은 퍼블릭 IP로 실행(assign_public_ip=true)", di.get("assign_public_ip") is True, str(di.get("assign_public_ip")))
    ami, out = pj("-var=enable_nat_instance=true", "-var=nat_ami_id=ami-0123456789abcdef0")
    if ami:
        is_("nat_ami_id 지정: 그 AMI를 쓰고 최신 AMI 조회를 하지 않는다",
            all(x["ami"] == "ami-0123456789abcdef0" for x in planned(ami, "aws_instance.nat")) and "nat_ami" not in json.dumps([r["address"] for r in ami["resource_changes"] if r["mode"] == "data"]))
    https, out = pj("-var=enable_nat_instance=true", f"-var=certificate_arn={FAKE_CERT}")
    if https:
        is_("certificate_arn 지정: 443 리스너 생성, 80번은 443으로 리다이렉트",
            n(https, "aws_lb_listener.https_default") == 1 and n(https, "aws_vpc_security_group_ingress_rule.alb_https") == 1
            and planned(https, "aws_lb_listener.default")[0]["default_action"][0]["type"] == "redirect")
        di = https["output_changes"]["deploy_inputs"]["after"]
        is_("certificate_arn 지정: 출력의 listener_protocol=HTTPS, certificate_arn 전달", di.get("listener_protocol") == "HTTPS" and di.get("certificate_arn") == FAKE_CERT, str(di.get("listener_protocol")))
    snap, out = pj("-var=enable_nat_instance=true", "-var=final_snapshot=false", "-var=db_multi_az=true", "-var=db_backup_retention_days=14")
    if snap:
        db = planned(snap, "aws_db_instance.this")[0]
        is_("final_snapshot=false, multi_az=true, 백업 14일이 반영된다", db["skip_final_snapshot"] is True and db["multi_az"] is True and db["backup_retention_period"] == 14)


def part_d():
    print("\n=== D. deploy.sh 함수 ===")
    if not BASH:
        say("env", "D bash를 찾지 못했습니다(Git for Windows 또는 bash 필요). 이 절을 건너뜁니다")
        return
    script = (INFRA / "scripts" / "deploy.sh").as_posix()
    fj = WORK / "foundation.json"
    fj.write_text(json.dumps(FAKE_FOUNDATION), encoding="utf-8")
    work = WORK.as_posix()

    def sh(code, extra_env=None):
        env = dict(os.environ)
        env["AWS_REGION"] = "sa-east-1"
        if extra_env:
            env.update(extra_env)
        # 실제 실행에서 경로는 /c/Users/... 형태다(deploy.sh가 cd && pwd로 구한다). 시험도 같은 형태로 넘긴다.
        # C:/... 형태로 넘기면 Git Bash의 경로 변환 문제(실제로 겪은 버그)를 가린다
        r = subprocess.run([BASH, "-c", f'source "{script}"; FOUNDATION_JSON="$(cygpath -u "{fj.as_posix()}" 2>/dev/null || echo "{fj.as_posix()}")"; {code}'],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        return r.returncode, r.stdout, r.stderr

    for good in ["abcd1234", "abcd", "a1b2"]:
        rc, _, _ = sh(f'valid_id {good}')
        say("ok" if rc == 0 else "fail", f"D deploy_id '{good}' 허용")
    for bad in ["abc", "abc-1234", "ABCD1234", "123456789", "ab_cd1"]:
        rc, _, _ = sh(f'valid_id {bad}')
        say("ok" if rc != 0 else "fail", f"D deploy_id '{bad}' 거부")
    rc, out, err = sh('db_param_name abcd1234; db_param_arn abcd1234; db_name_of abcd1234')
    lines = out.split()
    want = ["/paved-clouds/apps/abcd1234/database-url",
            "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/abcd1234/database-url", "app_abcd1234"]
    say("ok" if lines == want else "fail", "D 앱 전용 DB 이름·파라미터·ARN 규칙", "" if lines == want else f"{lines} {err[:80]}")

    sample = ("connecting to mysql://paved_admin:Sup3rS3cretPw@host:3306/app\nAKIAABCDEFGHIJKLMNOP\npassword=hunter2 token: abc123\n"
              "GET /health 200 OK")
    rc, out, err = sh(f"printf '%s' '{sample}' | mask")
    masked_ok = all(s not in out for s in ["Sup3rS3cretPw", "ABCDEFGHIJKLMNOP", "hunter2", "abc123"]) and "GET /health 200 OK" in out
    say("ok" if masked_ok else "fail", "D 로그 마스킹: 비밀은 가리고 정상 로그는 남긴다", "" if masked_ok else out[:120])

    for mode, want_secrets, need_text in [("provision", {"DB_ADMIN_PASSWORD", "APP_URL"}, "CREATE DATABASE"),
                                          ("drop", {"DB_ADMIN_PASSWORD"}, "DROP DATABASE"),
                                          ("verify", {"APP_URL"}, "OTHER_DB")]:
        rc, out, err = sh(f'db_taskdef_json {mode} abcd1234')
        try:
            td = json.loads(out)
            c = td["containerDefinitions"][0]
            secrets = {s["name"] for s in c["secrets"]}
            script_text = c["command"][0]
            ok = (secrets == want_secrets and need_text in script_text and td["family"] == f"paved-clouds-dbinit-abcd1234-{mode}"
                  and c["image"].endswith(":tools-mysql84") and td["networkMode"] == "awsvpc"
                  and all(s["valueFrom"].startswith("arn:aws:ssm:") for s in c["secrets"])
                  # 입력 값이 그대로 들어갔는지. Git Bash는 /로 시작하는 환경 변수 값(로그 그룹 이름 등)을
                  # Windows 경로(C:/Program Files/Git/...)로 바꾸고, 이러면 AWS가 작업 정의 등록을 거부한다
                  and c["logConfiguration"]["options"]["awslogs-group"] == FAKE_FOUNDATION["db_provisioner_log_group"]
                  and c["logConfiguration"]["options"]["awslogs-region"] == "sa-east-1"
                  and c["image"] == FAKE_FOUNDATION["ecr_repository_url"] + ":tools-mysql84"
                  and td["executionRoleArn"] == FAKE_FOUNDATION["execution_role_arn"]
                  and {x["valueFrom"] for x in c["secrets"]} <= {FAKE_FOUNDATION["db_admin_password_parameter_arn"],
                                                                 "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/abcd1234/database-url"})
            # 접속 정보는 secrets(valueFrom ARN)로만 전달되고 평문 비밀번호는 JSON에 없다
            ok = ok and not any(k in json.dumps(c["environment"]) for k in ["PASSWORD", "APP_URL"])
            say("ok" if ok else "fail", f"D DB 작업 정의({mode}): secrets {sorted(secrets)}, 평문 비밀번호 없음", "" if ok else out[:160])
        except Exception as e:  # noqa: BLE001
            say("fail", f"D DB 작업 정의({mode}) JSON", f"{e} {err[:100]}")

    sdir = WORK / "state_test"
    sdir.mkdir(parents=True, exist_ok=True)
    rc, out, err = sh(f'state_setup "{sdir.as_posix()}" abcd1234', {"PAVED_STATE_BUCKET": "my-state-bucket"})
    ov = (sdir / "backend_override.tf")
    txt = ov.read_text(encoding="utf-8") if ov.exists() else ""
    ok = 'bucket       = "my-state-bucket"' in txt and "deployments/abcd1234/terraform.tfstate" in txt and "use_lockfile = true" in txt and "encrypt      = true" in txt
    say("ok" if ok else "fail", "D PAVED_STATE_BUCKET이 있으면 S3 backend override를 쓴다(암호화·잠금 포함)", "" if ok else (txt or err)[:100])
    ov.unlink(missing_ok=True)
    rc, out, err = sh(f'state_setup "{sdir.as_posix()}" abcd1234', {"PAVED_STATE_BUCKET": ""})
    say("ok" if not ov.exists() else "fail", "D PAVED_STATE_BUCKET이 없으면 로컬 state(override를 만들지 않음)")

    for text, want in [("dnf invoked oom-killer\nOut of memory: Killed process 2036 (dnf)", "FAIL"),
                       ("cloud-init: Complete!\nCreated symlink /etc/systemd/x.wants/iptables.service -> /usr/lib/systemd/system/iptables.service.", "OK"),
                       ("", "UNKNOWN")]:
        code = (f"aws() {{ printf '%s' '{text}'; }}; nat_check_one i-123 sa-east-1")
        rc, out, err = sh(code)
        say("ok" if out.strip() == want else "fail", f"D NAT 부팅 로그 판정 → {want}", out.strip()[:60])


    # ---- 롤백·이력·DB 전환·정지 태스크 집계 시나리오 (Codex 리뷰 지적 1~5) ----
    def P(path):
        """bash 안에서 쓸 POSIX 경로 표현. 실제 실행과 같은 /c/... 형태로 시험한다"""
        return f'"$(cygpath -u "{Path(path).as_posix()}" 2>/dev/null || echo "{Path(path).as_posix()}")"'

    hist = WORK / "hist"
    hist.mkdir(parents=True, exist_ok=True)

    def prev(history_lines, current_image):
        code = (f'd={P(hist)}; printf "%s\\n" {" ".join(repr(x) for x in history_lines)} > "$d/history.log"; '
                f'tf() {{ echo "{current_image}"; }}; previous_image "$d"')
        rc, out, err = sh(code)
        return out.strip()

    h1 = ["2026-01-01T00:00:00Z|IMG:r1|s1", "2026-01-02T00:00:00Z|IMG:r2|s2"]
    say("ok" if prev(h1, "IMG:r2") == "IMG:r1" else "fail", "D 롤백 대상: 정상 업데이트(r1→r2) 뒤에는 r1")
    got = prev(["2026-01-01T00:00:00Z|IMG:r1|s1"], "IMG:r2")
    say("ok" if got == "IMG:r1" else "fail", "D 롤백 대상: 업데이트가 실패해 이력에 없는 r2가 배포된 상태에서도 마지막 정상 r1(옛 코드는 r1을 제외)", got)
    got = prev(["2026-01-01T00:00:00Z|IMG:r0|s0", "2026-01-02T00:00:00Z|IMG:r1|s1"], "IMG:r2")
    say("ok" if got == "IMG:r1" else "fail", "D 롤백 대상: 이력이 더 길어도 r1(더 옛날 r0로 건너뛰지 않음)", got)
    got = prev(h1 + ["2026-01-03T00:00:00Z|IMG:r1|s3"], "IMG:r1")
    say("ok" if got == "IMG:r2" else "fail", "D 롤백 대상: 롤백한 뒤(r1→r2→r1)에는 그 직전의 r2", got)

    # 스냅샷: 정상일 때의 앱 설정과 이미지가 롤백에서 함께 복원되는지
    sdir = WORK / "snap"
    sdir.mkdir(parents=True, exist_ok=True)
    app0 = {"app": {"container_port": 8000, "health_check_path": "/health", "task_size": "xsmall", "min_tasks": 1, "max_tasks": 1,
                    "use_database": True, "environment": {"COOKIE_SECURE": "true"}}}
    plat0 = {"platform": {"region": "sa-east-1", "deploy_id": "abcd1234", "image": "IMG:good", "cpu_architecture": "X86_64",
                          "listener_port": 8001, "health_check_grace_seconds": 90, "foundation": {"alb_dns_name": "OLD"}}}
    (sdir / "app.auto.tfvars.json").write_text(json.dumps(app0), encoding="utf-8")
    (sdir / "platform.auto.tfvars.json").write_text(json.dumps(plat0), encoding="utf-8")
    rc, out, err = sh(f'd={P(sdir)}; snapshot_healthy "$d"')
    stamp = out.strip()
    app1 = copy.deepcopy(app0); app1["app"].update(container_port=9999, health_check_path="/nope", task_size="medium", environment={"X": "1"})
    plat1 = copy.deepcopy(plat0); plat1["platform"].update(image="IMG:bad", cpu_architecture="ARM64", health_check_grace_seconds=5)
    plat1["platform"]["foundation"] = {"alb_dns_name": "NEW"}
    (sdir / "app.auto.tfvars.json").write_text(json.dumps(app1), encoding="utf-8")
    (sdir / "platform.auto.tfvars.json").write_text(json.dumps(plat1), encoding="utf-8")
    rc, out, err = sh(f'd={P(sdir)}; restore_snapshot "$d" "{stamp}"')
    app2 = json.loads((sdir / "app.auto.tfvars.json").read_text(encoding="utf-8"))
    plat2 = json.loads((sdir / "platform.auto.tfvars.json").read_text(encoding="utf-8"))
    say("ok" if app2 == app0 else "fail", "D 롤백 스냅샷: 앱 설정 전체(포트, 헬스체크 경로, 크기, 환경 변수)가 정상이던 때로 복원", str(app2)[:100])
    ok = (plat2["platform"]["image"] == "IMG:good" and plat2["platform"]["cpu_architecture"] == "X86_64"
          and plat2["platform"]["health_check_grace_seconds"] == 90)
    say("ok" if ok else "fail", "D 롤백 스냅샷: 이미지·아키텍처·유예 시간도 복원")
    say("ok" if plat2["platform"]["foundation"] == {"alb_dns_name": "NEW"} else "fail", "D 롤백 스냅샷: foundation 값은 지금 것을 유지(낡은 값으로 되돌리지 않음)")
    rc, out, err = sh(f'd={P(sdir)}; printf "%s|%s|%s\\n" t IMG:good "{stamp}" > "$d/history.log"; snapshot_of_image "$d" IMG:good; snapshot_of_image "$d" IMG:none')
    say("ok" if out.strip() == stamp else "fail", "D 이미지로 스냅샷을 찾는다(없는 이미지는 빈 값 = 옛 이력은 이미지만 복원하고 경고)", out.strip()[:60])

    # 이번 배포가 시작한 태스크만 센다
    alog = WORK / "aws-args.log"
    mock_aws = ('aws() { echo "$@" >> ' + P(alog) + '; case "$*" in *"--started-by ecs-svc/CUR"*) echo 1;; '
                '*"--service-name"*) echo 5;; *) echo 9;; esac; }')
    rc, out, err = sh(f'{mock_aws}; count_stopped_tasks sa-east-1 paved-clouds ecs-svc/CUR; count_stopped_tasks sa-east-1 paved-clouds ""; count_stopped_tasks sa-east-1 paved-clouds None')
    args = alog.read_text(encoding="utf-8") if alog.exists() else ""
    ok = out.split() == ["1", "0", "0"] and "--started-by ecs-svc/CUR" in args and "--service-name" not in args
    say("ok" if ok else "fail", "D 정지 태스크는 이번 배포(--started-by)만 센다. 서비스 전체를 세지 않는다. 배포 ID가 없으면 0", f"{out.split()} {args[:80]}")

    # 업데이트로 DB를 켜면 앱 전용 DB로 전환 (공유 관리자 URL로 조용히 대체되지 않게)
    def db_case(markers, app_use_db):
        ddir = WORK / ("dbsw_" + "_".join(markers or ["none"]) + str(app_use_db))
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "platform.auto.tfvars.json").write_text(json.dumps({"platform": {"deploy_id": "abcd1234", "database_url_parameter_arn": "", "foundation": {}}}), encoding="utf-8")
        appf = ddir / "newapp.json"
        appf.write_text(json.dumps({"container_port": 8000, "use_database": app_use_db}), encoding="utf-8")
        for m in markers:
            (ddir / m).write_text("x", encoding="utf-8")
        rc, out, err = sh(f'export_foundation() {{ :; }}; ensure_db_isolation {P(ddir)} abcd1234 {P(appf)}')
        param = json.loads((ddir / "platform.auto.tfvars.json").read_text(encoding="utf-8"))["platform"]["database_url_parameter_arn"]
        return (ddir / "db-isolated").exists(), param, err

    iso, param, err = db_case([], True)
    ok = iso and param.endswith("parameter/paved-clouds/apps/abcd1234/database-url")
    say("ok" if ok else "fail", "D DB 없이 만든 배포를 update로 DB 사용으로 바꾸면 앱 전용 DB(app_abcd1234)로 전환", f"{iso} {param} {err[:60]}")
    iso, param, err = db_case(["db-shared"], True)
    say("ok" if (not iso and param == "") else "fail", "D 처음부터 --shared-db로 만든 배포는 그 선택을 유지(몰래 전환하지 않음)")
    iso, param, err = db_case([], False)
    say("ok" if (not iso and param == "") else "fail", "D DB를 쓰지 않으면 아무것도 바꾸지 않는다")
    iso, param, err = db_case(["db-isolated"], True)
    say("ok" if (iso and param == "") else "fail", "D 이미 앱 전용 DB면 그대로(파라미터를 다시 쓰지 않음)")

    # 시도 기록: 실패도 사유와 함께 남는다
    adir = WORK / "attempts"
    adir.mkdir(parents=True, exist_ok=True)
    rc, out, err = sh(f'd={P(adir)}; record_attempt "$d" ok IMG:r1 ""; record_attempt "$d" fail IMG:r2 "시간 초과 | 줄바꿈\nx"')
    lines = (adir / "attempts.log").read_text(encoding="utf-8").splitlines() if (adir / "attempts.log").exists() else []
    ok = len(lines) == 2 and lines[0].split("|")[1:3] == ["ok", "IMG:r1"] and lines[1].split("|")[1:3] == ["fail", "IMG:r2"] and lines[1].count("|") == 3
    say("ok" if ok else "fail", "D 시도 기록: 성공과 실패(사유 포함)가 한 줄씩 남고 사유의 | 와 줄바꿈이 줄 형식을 깨지 않는다", str(lines)[:120])


def part_e():
    print("\n=== E. 정적 검사 ===")
    code, out = tf(INFRA, "fmt", "-check", "-recursive")
    say("ok" if code == 0 else "fail", "E terraform fmt -check", out.strip()[:100])
    for name in ["foundation", "deployments/_template", "bootstrap"]:
        # 템플릿은 모듈 상대 경로(../../modules)가 맞도록 deployments 바로 아래에서 검증한다
        dst = VAL_DIR if name == "deployments/_template" else WORK / ("v_" + name)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(INFRA / name, dst, ignore=COPY_IGNORE)
        code, out = tf(dst, "init", "-input=false", "-backend=false")
        if code != 0:
            say("env", f"E {name} init", out.strip()[:120].replace("\n", " "))
            continue
        code, out = tf(dst, "validate", "-no-color")
        say("ok" if code == 0 else "fail", f"E terraform validate: {name}", "" if code == 0 else out.strip()[:120].replace("\n", " "))
    if BASH:
        r = subprocess.run([BASH, "-n", (INFRA / "scripts" / "deploy.sh").as_posix()], capture_output=True, text=True)
        say("ok" if r.returncode == 0 else "fail", "E deploy.sh 문법", r.stderr.strip()[:100])
    else:
        say("env", "E deploy.sh 문법: bash를 찾지 못했습니다")


def main():
    global BASH
    BASH = find_bash()
    for p in (WORK, MOD_DIR, VAL_DIR):
        shutil.rmtree(p, ignore_errors=True)
    WORK.mkdir(parents=True)
    try:
        part_e()
        mod = prepare_module_dir()
        part_a(mod)
        part_b(mod)
        fnd = prepare_foundation_dir()
        part_c(fnd, aws_ready())
        part_d()
    finally:
        for p in (WORK, MOD_DIR, VAL_DIR):
            shutil.rmtree(p, ignore_errors=True)
    print(f"\n결과: 통과 {COUNTS['ok']}, 실패 {COUNTS['fail']}, 시험환경오류 {COUNTS['env']}, 건너뜀 {COUNTS['skip']}")
    if FAILS:
        print("문제가 있는 항목:")
        for f in FAILS:
            print("  -", f)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
