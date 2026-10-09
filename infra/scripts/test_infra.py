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


def expect(name, want, code, out, why=None):
    """why: 차단을 기대할 때 출력에 반드시 있어야 하는 문구(의도한 검증이 막았는지 확인). 다른 검증 오류로 막힌 것을 통과로 세지 않는다"""
    got = classify(code, out)
    if got == "시험환경오류":
        say("env", name, out.strip().replace("\n", " ")[:160])
    elif got == want and want == "차단" and why and why not in out:
        say("fail", f"{name} (다른 이유로 막힘: '{why}' 없음)", out.strip().replace("\n", " ")[:160])
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
    "db_name": "app",
    "db_provisioner_execution_role_arn": "arn:aws:iam::123456789012:role/paved-clouds-db-provisioner-execution",
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


def case_module(d, name, want, pm=None, am=None, why=None):
    p, a = copy.deepcopy(BASE_P), copy.deepcopy(BASE_A)
    if pm:
        pm(p)
    if am:
        am(a)
    code, out = run_module(d, p, a)
    expect("A " + name, want, code, out, why)
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
        case_module(d, n, "차단", am=lambda a, k=k, v=v: a.update({k: v}), why=k)
    case_module(d, "min_tasks=3", "차단", am=lambda a: a.update(min_tasks=3, max_tasks=3))
    case_module(d, "max_tasks=5", "차단", am=lambda a: a.update(max_tasks=5))
    case_module(d, "min_tasks=2,max_tasks=1", "차단", am=lambda a: a.update(min_tasks=2, max_tasks=1))
    for k in ["DATABASE_URL", "DB_PASSWORD", "API_KEY", "lower"]:
        case_module(d, f"environment에 {k}", "차단", am=lambda a, k=k: a["environment"].update({k: "x"}))
    case_module(d, "environment 21개", "차단", am=lambda a: a.update(environment={f"K{i}": "v" for i in range(21)}))
    case_module(d, "image :latest", "차단", lambda p: p.update(image=ECR + ":latest"), why="latest")
    case_module(d, "image 태그 없음", "차단", lambda p: p.update(image=ECR))
    case_module(d, "image docker.io", "차단", lambda p: p.update(image="docker.io/library/nginx:1"))
    case_module(d, "image 다른 ECR 저장소", "차단", lambda p: p.update(image="111122223333.dkr.ecr.sa-east-1.amazonaws.com/other/x:1"))
    for n, v in [("listener_port=9000", 9000), ("listener_port=80", 80), ("listener_port=8001.5", 8001.5)]:
        case_module(d, n, "차단", lambda p, v=v: p.update(listener_port=v))
    case_module(d, "deploy_id='ab'", "차단", lambda p: p.update(deploy_id="ab", image=ECR + ":ab-r1"))
    case_module(d, "deploy_id='abc-1234'(하이픈)", "차단", lambda p: p.update(deploy_id="abc-1234"))
    case_module(d, "deploy_id 대문자", "차단", lambda p: p.update(deploy_id="ABCD1234"))
    case_module(d, "cpu_architecture='arm'", "차단", lambda p: p.update(cpu_architecture="arm"), why="cpu_architecture")
    case_module(d, "use_database=true, 파라미터 ARN 빈값", "차단", lambda p: p["foundation"].update(database_url_parameter_arn=""))
    case_module(d, "health_check_grace_seconds=601", "차단", lambda p: p.update(health_check_grace_seconds=601), why="health_check_grace_seconds")
    case_module(d, "database_url_parameter_arn 형식 오류", "차단", lambda p: p.update(database_url_parameter_arn="not-an-arn"))
    case_module(d, "listener_protocol='FTP'", "차단", lambda p: p["foundation"].update(listener_protocol="FTP"), why="listener_protocol")
    case_module(d, "HTTPS인데 certificate_arn 비어 있음", "차단", lambda p: p["foundation"].update(listener_protocol="HTTPS"), why="certificate_arn")
    # 앱 전용 DB 접속 정보는 이 배포의 것이어야 한다(격리를 모듈이 강제)
    other = "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/other123/database-url"
    case_module(d, "다른 앱의 DB 접속 정보 ARN", "차단", lambda p: p.update(database_url_parameter_arn=other), why="database_url_parameter_arn")
    case_module(d, "공유 관리자 DB URL ARN을 앱 전용 값으로 지정", "차단",
                lambda p: p.update(database_url_parameter_arn=FAKE_FOUNDATION["database_url_parameter_arn"]), why="database_url_parameter_arn")
    case_module(d, "정상: 이 배포의 앱 전용 DB 접속 정보 ARN", "통과",
                lambda p: p.update(database_url_parameter_arn="arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/abcd1234/database-url"))
    case_module(d, "deregistration_delay_seconds=301", "차단", lambda p: p.update(deregistration_delay_seconds=301), why="deregistration_delay_seconds")
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
    inv("ecr_keep_images=5", R, "-var=ecr_keep_images=5")

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
    # IAM 정책은 다른 리소스의 ARN이 apply 때 정해져서 계획에서 값을 볼 수 없다. 그래서 정책 문서 블록 단위로 소스를 확인한다(소스 확인)
    iam = (INFRA / "foundation" / "iam.tf").read_text(encoding="utf-8")
    app_doc = iam.split('data "aws_iam_policy_document" "read_secrets"')[1].split("\n}\n")[0]
    prov_doc = iam.split('data "aws_iam_policy_document" "db_provisioner_read"')[1].split("\n}\n")[0]
    is_("기본(소스 확인): 앱 공유 실행 역할은 앱별 접속 정보(/apps/*)를 읽지만 DB 관리자 비밀번호는 읽지 못한다",
        "apps_param_arn" in app_doc and "db_admin_password" not in app_doc)
    is_("기본(소스 확인): 관리자 비밀번호는 DB 작업 전용 역할만 읽는다", "db_admin_password" in prov_doc)
    is_("기본: DB 작업 전용 실행 역할이 만들어진다", n(base, "aws_iam_role.db_provisioner_execution") == 1)
    pol = json.loads(planned(base, "aws_ecr_lifecycle_policy.apps")[0]["policy"])
    r1, r2 = pol["rules"][0]["selection"], pol["rules"][1]["selection"]
    is_("기본: ECR 보관 정책은 앱 이미지 200개, tools- 이미지는 별도 규칙(우선순위가 더 앞)",
        r1.get("tagPrefixList") == ["tools-"] and pol["rules"][0]["rulePriority"] < pol["rules"][1]["rulePriority"]
        and r2["countNumber"] == 200 and r2["tagStatus"] == "any", str(pol)[:160])

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
              "PASSWORD=UpperPw1 Api-Key: UpperKey2 API_KEY=UpperKey3\n"
              "GET /health 200 OK")
    rc, out, err = sh(f"printf '%s' '{sample}' | mask")
    masked_ok = all(s not in out for s in ["Sup3rS3cretPw", "ABCDEFGHIJKLMNOP", "hunter2", "abc123", "UpperPw1", "UpperKey2", "UpperKey3"]) and "GET /health 200 OK" in out
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
                  # 관리자 비밀번호는 앱이 쓰는 공유 실행 역할이 아니라 DB 작업 전용 역할만 읽는다
                  and td["executionRoleArn"] == FAKE_FOUNDATION["db_provisioner_execution_role_arn"]
                  and {e["name"]: e["value"] for e in c["environment"]}.get("OTHER_DB") == FAKE_FOUNDATION["db_name"]
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


    # ---- 롤백 대상을 이미지가 아니라 배포 항목(이미지 + 앱 설정)으로 고르는지 (Codex 재리뷰 지적) ----
    def entry_case(name, history, applied, want):
        """history: [(image, stamp, app_config)], applied: (image, app_config) 또는 None(보관본 없음)"""
        # 폴더 이름은 ASCII로 한다. 설명 문장(한글·화살표)을 쓰면 Windows에서 Python이 경로를 열지 못한다
        entry_case.n = getattr(entry_case, "n", 0) + 1
        edir = WORK / f"entry_{entry_case.n}"
        (edir / "history").mkdir(parents=True, exist_ok=True)
        lines = []
        for n, (image, stamp, cfg) in enumerate(history):
            if stamp:
                (edir / "history" / f"{stamp}.app.json").write_text(json.dumps({"app": cfg}), encoding="utf-8")
            lines.append(f"2026-01-0{n + 1}T00:00:00Z|{image}|{stamp}")
        # 줄 끝을 \n으로 고정한다. Windows에서 write_text는 \r\n으로 써서 마지막 칸(스냅샷 이름)에 \r이 붙는다.
        # deploy.sh는 bash printf로 쓰므로 실제 이력에는 \r이 없다
        (edir / "history.log").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        if applied:
            (edir / "applied.app.json").write_text(json.dumps({"app": applied[1]}), encoding="utf-8")
            (edir / "applied.platform.json").write_text(json.dumps({"platform": {"image": applied[0]}}), encoding="utf-8")
        rc, out, err = sh(f'tf() {{ echo "IMG:r9"; }}; previous_entry {P(edir)}')
        got = out.strip()
        say("ok" if got == want else "fail", "D 롤백 대상(배포 항목 기준): " + name, f"기대 {want!r} 실제 {got!r} {err[:60]}")

    c1 = {"container_port": 8000, "health_check_path": "/health", "environment": {"A": "1"}}
    c2 = {"container_port": 8000, "health_check_path": "/health2", "environment": {"A": "2"}}
    c3 = {"container_port": 9000, "health_check_path": "/health", "environment": {"A": "1"}}
    entry_case("이미지는 그대로(r1)이고 설정만 바꾼 업데이트 뒤 → 직전 설정의 항목(옛 코드는 대상을 못 찾음)",
               [("IMG:r1", "s1", c1), ("IMG:r1", "s2", c2)], ("IMG:r1", c2), "IMG:r1|s1")
    entry_case("설정만 바꾼 업데이트가 여러 번(c1→c2→c3) → 직전(c2) 항목",
               [("IMG:r1", "s1", c1), ("IMG:r1", "s2", c2), ("IMG:r1", "s3", c3)], ("IMG:r1", c3), "IMG:r1|s2")
    entry_case("마지막으로 apply한 것이 실패한 설정 변경(이력에 없음) → 마지막 정상 항목",
               [("IMG:r1", "s1", c1)], ("IMG:r1", c2), "IMG:r1|s1")
    entry_case("이미지와 설정이 모두 같은 중복 항목은 건너뛴다",
               [("IMG:r0", "s0", c3), ("IMG:r1", "s1", c1), ("IMG:r1", "s2", c1)], ("IMG:r1", c1), "IMG:r0|s0")
    entry_case("이미지가 다르면 대상(이미지 r1→r2)", [("IMG:r1", "s1", c1), ("IMG:r2", "s2", c1)], ("IMG:r2", c1), "IMG:r1|s1")
    entry_case("보관본(applied)이 없는 옛 배포는 이미지끼리 비교(현재 r9, 스냅샷 없는 옛 이력)",
               [("IMG:r1", "", None), ("IMG:r9", "", None)], None, "IMG:r1|")

    # 정지 태스크 조회가 실패하면 조용히 0으로 넘기지 않고 경고를 낸다
    rc, out, err = sh('aws() { echo "InvalidParameterException: 거부됨" >&2; return 255; }; count_stopped_tasks sa-east-1 paved-clouds ecs-svc/CUR')
    ok = out.strip() == "0" and "조회하지 못했습니다" in err and "InvalidParameterException" in err
    say("ok" if ok else "fail", "D 정지 태스크 조회가 실패하면 0으로 대신하되 경고를 낸다(조용히 숨기지 않음)", err[:100])


    # ---- 리뷰 지적 수정: ID 검증, 폴더 정리, 입력 되돌리기, foundation 갱신, ECR 확인, 헬스체크 대기 ----
    # 배포 ID가 아닌 값으로 destroy/status/rollback을 부르면 deployments 밖 폴더(foundation)를 건드린다
    for sub in ["cmd_destroy ../foundation --yes", "cmd_status ../foundation", "cmd_rollback ../foundation --plan-only", "cmd_update .. --image x"]:
        rc, out, err = sh('terraform() { echo TERRAFORM_CALLED; }; aws() { echo AWS_CALLED; }; ' + sub)
        say("ok" if rc != 0 and "TERRAFORM_CALLED" not in out and "AWS_CALLED" not in out and "deploy id" in err else "fail",
            f"D 배포 ID가 아닌 값은 거부(terraform 실행 없음): {sub.split()[0]} {sub.split()[1]}", f"rc={rc} {err[:80]}")

    # up이 쓸 폴더: 삭제 끝난 폴더는 보관 폴더로 옮겨 같은 ID를 다시 쓸 수 있고, 아직 살아 있는 배포는 거부
    croot = WORK / "claimroot"
    (croot / "deployments" / "abcd1234").mkdir(parents=True, exist_ok=True)
    rc, out, err = sh(f'ROOT={P(croot)}; claim_deploy_dir abcd1234')
    say("ok" if rc != 0 and "이미 있는 배포" in err else "fail", "D 아직 삭제하지 않은 같은 ID 폴더는 거부", err[:80])
    (croot / "deployments" / "abcd1234" / "destroyed").write_text("x", encoding="utf-8")
    rc, out, err = sh(f'ROOT={P(croot)}; claim_deploy_dir abcd1234')
    arch = list((croot / "deployments" / "_destroyed").glob("abcd1234-*")) if (croot / "deployments" / "_destroyed").exists() else []
    say("ok" if rc == 0 and len(arch) == 1 and not (croot / "deployments" / "abcd1234").exists() else "fail",
        "D destroy가 끝난 폴더는 보관 폴더로 옮기고 같은 ID를 다시 쓸 수 있다", f"rc={rc} {err[:80]}")

    # up이 apply 전에 실패하면 만들다 만 폴더를 지우고, apply가 시작됐거나 성공했으면 지우지 않는다
    for phase, status, want_exists, label in [("prep", "1", False, "apply 전에 실패하면 폴더를 지운다"),
                                              ("", "1", True, "apply가 시작된 뒤 실패하면(state가 있을 수 있음) 지우지 않는다"),
                                              ("prep", "0", True, "성공(--plan-only 포함)이면 지우지 않는다")]:
        cdir = WORK / ("cleanup_" + (phase or "none") + status)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "terraform.tfstate").write_text("{}", encoding="utf-8")
        rc, out, err = sh(f'UP_DIR={P(cdir)}; UP_PHASE="{phase}"; set +e; (exit {status}); cleanup_failed_up')
        say("ok" if cdir.exists() == want_exists else "fail", "D up 정리: " + label, f"존재={cdir.exists()}")

    # --plan-only만 하고 버린 변경이 다음 계획에 섞이지 않는다
    rdir = WORK / "reset1"
    rdir.mkdir(parents=True, exist_ok=True)
    good_app = {"app": {"container_port": 8000, "use_database": False}}
    good_plat = {"platform": {"deploy_id": "abcd1234", "image": "IMG:good", "database_url_parameter_arn": "", "foundation": {"x": 1}}}
    (rdir / "applied.app.json").write_text(json.dumps(good_app), encoding="utf-8")
    (rdir / "applied.platform.json").write_text(json.dumps(good_plat), encoding="utf-8")
    (rdir / "app.auto.tfvars.json").write_text(json.dumps({"app": {"container_port": 9999, "use_database": True}}), encoding="utf-8")
    (rdir / "platform.auto.tfvars.json").write_text(json.dumps({"platform": {"deploy_id": "abcd1234", "image": "IMG:discarded", "database_url_parameter_arn": "arn:x", "foundation": {"x": 2}}}), encoding="utf-8")
    (rdir / "tfplan").write_text("x", encoding="utf-8")
    (rdir / "db-isolated").write_text("app_abcd1234\n", encoding="utf-8")   # 버린 계획이 켠 표식(provisioned 아님)
    rc, out, err = sh(f'reset_inputs {P(rdir)}')
    a = json.loads((rdir / "app.auto.tfvars.json").read_text(encoding="utf-8"))
    pl = json.loads((rdir / "platform.auto.tfvars.json").read_text(encoding="utf-8"))
    say("ok" if a == good_app and pl == good_plat and not (rdir / "tfplan").exists() and not (rdir / "db-isolated").exists() else "fail",
        "D 입력 되돌리기: 버린 계획의 이미지·앱 설정·앱 전용 DB 표식·저장된 계획이 남지 않는다", f"{a} {pl}"[:120])
    rdir2 = WORK / "reset2"
    rdir2.mkdir(parents=True, exist_ok=True)
    plat_db = {"platform": {"deploy_id": "abcd1234", "image": "IMG:good", "database_url_parameter_arn": "arn:aws:ssm:sa-east-1:123456789012:parameter/paved-clouds/apps/abcd1234/database-url", "foundation": {}}}
    (rdir2 / "applied.app.json").write_text(json.dumps(good_app), encoding="utf-8")
    (rdir2 / "applied.platform.json").write_text(json.dumps(plat_db), encoding="utf-8")
    (rdir2 / "app.auto.tfvars.json").write_text("{}", encoding="utf-8")
    (rdir2 / "platform.auto.tfvars.json").write_text("{}", encoding="utf-8")
    rc, out, err = sh(f'reset_inputs {P(rdir2)}')
    say("ok" if (rdir2 / "db-isolated").exists() else "fail", "D 입력 되돌리기: 배포된 상태가 앱 전용 DB를 쓰면 표식을 맞춘다", err[:80])
    rdir3 = WORK / "reset3"
    rdir3.mkdir(parents=True, exist_ok=True)
    (rdir3 / "platform.auto.tfvars.json").write_text("KEEP", encoding="utf-8")
    rc, out, err = sh(f'reset_inputs {P(rdir3)}')
    say("ok" if (rdir3 / "platform.auto.tfvars.json").read_text(encoding="utf-8") == "KEEP" else "fail", "D 입력 되돌리기: apply된 적 없는 배포(up --plan-only 직후)는 그대로 둔다")

    # update·rollback이 foundation 값을 지금 출력으로 갱신한다
    fdir = WORK / "fresh"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "platform.auto.tfvars.json").write_text(json.dumps({"platform": {"deploy_id": "abcd1234", "image": "IMG:good", "foundation": {"alb_dns_name": "OLD", "listener_protocol": "HTTP"}}}), encoding="utf-8")
    new_f = dict(FAKE_FOUNDATION, listener_protocol="HTTPS", certificate_arn=FAKE_CERT)
    fj.write_text(json.dumps(new_f), encoding="utf-8")
    rc, out, err = sh(f'export_foundation() {{ :; }}; refresh_foundation {P(fdir)}')
    pl = json.loads((fdir / "platform.auto.tfvars.json").read_text(encoding="utf-8"))["platform"]
    say("ok" if pl["foundation"] == new_f and pl["image"] == "IMG:good" else "fail", "D update/rollback은 foundation 값을 지금 출력(HTTPS 등)으로 갱신하고 나머지 입력은 유지", err[:80])
    fj.write_text(json.dumps(FAKE_FOUNDATION), encoding="utf-8")

    # 이미지 변경은 JSON으로 한다(sed였을 때는 &, 역슬래시가 치환 문법으로 해석됨)
    idir = WORK / "imgset"
    idir.mkdir(parents=True, exist_ok=True)
    (idir / "platform.auto.tfvars.json").write_text(json.dumps({"platform": {"image": "OLD"}}), encoding="utf-8")
    rc, out, err = sh(f'd={P(idir)}; set_platform_field "$d" image "a&b:tag"; input_image "$d"')
    say("ok" if out.strip() == "a&b:tag" else "fail", "D 이미지 값 교체: & 가 있어도 그대로 들어간다(sed 치환이었다면 깨짐)", out.strip()[:60])

    # ECR에 이미지가 있는지(없는 이미지로 배포하면 서킷 브레이커까지 8분 넘게 걸림)
    ilog = WORK / "ecr-args.log"
    img = "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps:abcd1234-r1"
    rc, out, err = sh(f'aws() {{ echo "$@" >> {P(ilog)}; echo "{{}}"; }}; check_image_exists "{img}"')
    args = ilog.read_text(encoding="utf-8") if ilog.exists() else ""
    say("ok" if rc == 0 and "--repository-name paved-clouds/apps" in args and "imageTag=abcd1234-r1" in args else "fail", "D ECR 이미지 확인: 태그 이미지를 올바른 저장소·태그로 조회", f"{rc} {args[:100]}")
    ilog.unlink(missing_ok=True)
    dg = "a" * 64
    rc, out, err = sh(f'aws() {{ echo "$@" >> {P(ilog)}; echo "{{}}"; }}; check_image_exists "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps@sha256:{dg}"')
    args = ilog.read_text(encoding="utf-8") if ilog.exists() else ""
    say("ok" if rc == 0 and ("imageDigest=sha256:" + dg) in args else "fail", "D ECR 이미지 확인: 다이제스트 이미지는 imageDigest로 조회", args[:100])
    rc, out, err = sh('aws() { echo "An error occurred (ImageNotFoundException) when calling the DescribeImages operation" >&2; return 254; }; check_image_exists "%s"' % img)
    say("ok" if rc != 0 and "ECR에 이미지가 없습니다" in err else "fail", "D ECR 이미지 확인: 없으면 배포 전에 중단(롤백 대상이 만료된 경우 포함)", err[:100])
    rc, out, err = sh('aws() { echo "An error occurred (AccessDeniedException)" >&2; return 254; }; check_image_exists "%s"' % img)
    say("ok" if rc != 0 and "확인하지 못했습니다" in err else "fail", "D ECR 이미지 확인: 권한 오류는 '없음'이 아니라 확인 실패로 구분", err[:100])

    # 포트 자동 할당: 조회가 실패하면 8001로 넘어가지 않고 중단
    rc, out, err = sh('aws() { return 255; }; pick_port')
    say("ok" if rc != 0 and out.strip() == "" and "조회하지 못했습니다" in err else "fail", "D 포트 자동 할당: 리스너 조회 실패를 숨기고 8001을 고르지 않는다", f"rc={rc} out={out.strip()} {err[:60]}")
    rc, out, err = sh('aws() { echo "80 8001 8002"; }; pick_port')
    say("ok" if out.strip() == "8003" else "fail", "D 포트 자동 할당: 쓰는 포트를 건너뛰고 가장 작은 빈 포트", out.strip())

    # S3 state: 버전 확인, 이미 있는 원격 state를 덮어쓰지 않는다
    for ver, want in [("1.9.8", False), ("1.10.0", True), ("1.16.5", True), ("1.10.0-beta1", True)]:
        rc, out, err = sh(f'tf_version() {{ echo {ver}; }}; require_tf_for_s3')
        say("ok" if (rc == 0) == want else "fail", f"D S3 state는 Terraform 1.10 이상만({ver})", err[:60])
    fdir2 = WORK / "fstate"

    def fstate_case(name, aws_body, local_state, want_rc, want_in_log, want_override):
        fdir2.mkdir(parents=True, exist_ok=True)
        for f in fdir2.glob("*"):
            f.unlink()
        if local_state:
            (fdir2 / "terraform.tfstate").write_text('{"version":4}', encoding="utf-8")
        tlog = WORK / "tf-calls.log"
        tlog.unlink(missing_ok=True)
        code = (f'FOUNDATION={P(fdir2)}; tf_version() {{ echo 1.16.5; }}; aws() {{ {aws_body}; }}; '
                f'terraform() {{ echo "$@" >> {P(tlog)}; }}; cmd_foundation_state')
        rc, out, err = sh(code, {"PAVED_STATE_BUCKET": "my-state-bucket"})
        calls = tlog.read_text(encoding="utf-8") if tlog.exists() else ""
        ok = (rc == 0) == want_rc and (want_in_log in calls if want_in_log else calls == "") and (fdir2 / "backend_override.tf").exists() == want_override
        say("ok" if ok else "fail", "D foundation-state: " + name, f"rc={rc} calls={calls.strip()[:80]} {err[:60]}")

    fstate_case("S3에 이미 있고 이 PC에도 로컬 state가 있으면 덮어쓰지 않고 중단", 'echo "{}"', True, False, "", False)
    fstate_case("S3에 이미 있고 로컬 state가 없으면 그 state를 연결(-reconfigure)", 'echo "{}"', False, True, "-reconfigure", True)
    fstate_case("S3에 없으면 로컬 state를 옮긴다(-migrate-state)", 'echo "An error occurred (404) when calling the HeadObject operation: Not Found" >&2; return 254', True, True, "-migrate-state", True)
    fstate_case("S3 확인이 권한 오류면 없다고 보지 않고 중단", 'echo "An error occurred (403) when calling the HeadObject operation: Forbidden" >&2; return 254', True, False, "", False)

    # DB 작업이 컨테이너 시작 전에 실패하면 이유를 보여 준다
    rc, out, err = sh("""region() { echo sa-east-1; }; ensure_tools_image() { :; }; db_taskdef_json() { echo "{}"; }
        awsn() { case "$*" in *register-task-definition*) echo arn:td;; *run-task*) echo arn:aws:ecs:sa-east-1:1:task/c/abc123;; *) :;; esac; }
        aws() { case "$*" in *"stoppedReason"*) printf 'CannotPullContainerError: image not found\\tNone\\n';; *exitCode*) echo None;; *) :;; esac; }
        db_task provision abcd1234""")
    say("ok" if rc != 0 and "CannotPullContainerError" in err and "컨테이너가 시작되지 못함" in err else "fail", "D DB 작업이 시작도 못 하면 종료 코드 None 대신 stoppedReason을 보여 준다", err[:160])

    # ---- 헬스체크 대기: 통과·실패 판정 (실제 AWS로만 확인했던 핵심 흐름) ----
    def wait_case(name, th_state, th_desc, rollout, want_rc, want_reason, timeout="1", stopped="0"):
        code = ('tf() { echo x; }; sleep() { :; }; '
                'aws() { case "$*" in *describe-target-health*) printf "%s\\t%s\\n" "$TH_STATE" "$TH_DESC";; '
                '*describe-services*) printf "%s\\t%s\\n" "$ROLLOUT" "ecs-svc/CUR";; *list-tasks*) echo "$STOPPED";; esac; }; '
                'wait_healthy abcd1234 && rc=0 || rc=$?; echo "RC=$rc REASON=$WAIT_REASON"')
        rc, out, err = sh(code, {"TH_STATE": th_state, "TH_DESC": th_desc, "ROLLOUT": rollout, "HEALTH_TIMEOUT": timeout, "STOPPED": stopped})
        got = out.strip().splitlines()[-1] if out.strip() else ""
        ok = f"RC={want_rc}" in got and want_reason in got
        say("ok" if ok else "fail", "D 헬스체크 대기: " + name, got[:120])

    wait_case("ECS 배포가 COMPLETED이고 대상이 healthy면 통과", "healthy", "None", "COMPLETED", 0, "")
    wait_case("대상이 healthy여도 롤아웃이 IN_PROGRESS면 통과하지 않는다(옛 태스크 때문에 일찍 통과하던 버그)", "healthy", "None", "IN_PROGRESS", 1, "시간 초과")
    wait_case("COMPLETED여도 대상이 unhealthy면 통과하지 않는다", "unhealthy", "None", "COMPLETED", 1, "시간 초과")
    wait_case("헬스체크가 4xx를 4번 연속 돌려주면 시간 초과를 기다리지 않고 실패", "unhealthy", "Health checks failed with these codes: [404]", "IN_PROGRESS", 1, "4xx", timeout="300")
    wait_case("5xx(시작 중 503)는 즉시 실패로 보지 않는다", "unhealthy", "Health checks failed with these codes: [503]", "IN_PROGRESS", 1, "시간 초과")
    wait_case("ECS 서킷 브레이커가 FAILED로 만들면 실패", "unhealthy", "None", "FAILED", 1, "서킷 브레이커", timeout="300")
    wait_case("이번 배포의 태스크가 3개 이상 종료되면 실패", "initial", "None", "IN_PROGRESS", 1, "반복해서 종료", timeout="300", stopped="3")
    wait_case("이번 배포의 정지 태스크가 2개면 계속 기다린다", "initial", "None", "IN_PROGRESS", 1, "시간 초과", stopped="2")


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
