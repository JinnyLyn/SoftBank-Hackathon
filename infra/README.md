# infra

Paved Clouds의 AWS 배포 계층이다. 담당: 이태훈

## 구조

| 경로 | 역할 | 누가 실행하나 |
|---|---|---|
| `bootstrap/` | Terraform state를 보관하는 S3 버킷(버전 관리, 암호화, 공개 차단, 잠금). foundation보다 오래 살아야 해서 따로 둔다 | 담당자가 최초 1회 |
| `foundation/` | 사전 생성 리소스: VPC, 서브넷, 보안 그룹(ALB·NAT·DB), 공유 ALB(HTTPS 선택), ECS 클러스터, ECR, 태스크 실행 역할, RDS MySQL, DB 접속 정보 파라미터 | 담당자가 행사 전에 1회 apply. 플랫폼은 건드리지 않는다 |
| `modules/ecs-web-app/` | 검증된 모듈. 배포 1건 = 앱 전용 보안 그룹, 로그 그룹, 태스크 정의, 대상 그룹, 전용 포트 리스너, ECS 서비스, 오토스케일링 | 배포 루트가 호출 |
| `modules/ecs-web-app/app-config.schema.json` | LLM 출력이 따라야 할 JSON 스키마. `variables.tf`의 LLM 입력과 규칙이 같다 | LLM 계층이 3·5단계 출력 검증에 사용 |
| `deployments/_template/` | 배포 건별 루트 모듈 템플릿 | 플랫폼이 `deployments/<deploy_id>/`로 복사해서 사용 |
| `scripts/deploy.sh` | 배포 1건을 만들고(`up`) 바꾸고(`update`) 되돌리고(`rollback`) 지우는(`destroy`) 자동화. 앱별 DB, 실패 분석 정보 수집(`diagnose`) 포함 | 플랫폼 백엔드 또는 담당자가 호출. 아래 "배포 스크립트" 참고 |
| `scripts/test_infra.py` | 회귀 시험(AWS에 리소스를 만들지 않음). 입력 검증, 계획 내용, 스크립트 함수, 정적 검사 | 인프라를 바꾼 뒤 실행: `python infra/scripts/test_infra.py` |

### modules/ecs-web-app 파일 안내 (AWS 서비스별)

| 파일 | AWS 서비스 | 내용 |
|---|---|---|
| `main.tf` | — | 모듈 개요, 리전 조회, 태스크 크기 프리셋, DB 접속 정보 선택 등 공용 `locals` |
| `cloudwatch.tf` | CloudWatch Logs | 배포 건별 로그 그룹 |
| `ecs.tf` | ECS Fargate | 태스크 정의(컨테이너 실행 명세), 서비스(서킷 브레이커 포함) |
| `alb.tf` | ELB | 대상 그룹, 이 배포 전용 포트 리스너(HTTPS면 인증서 포함) |
| `security_groups.tf` | VPC 보안 그룹 | 앱 전용 보안 그룹과 ALB·DB 쪽 허용 규칙 |
| `autoscaling.tf` | Application Auto Scaling | 태스크 수 범위, CPU 기준 자동 증감 |
| `variables.tf`, `outputs.tf`, `versions.tf`, `app-config.schema.json` | — | 입력 변수, 출력값, provider 버전, LLM 출력 스키마 |

### foundation 파일 안내 (AWS 서비스별)

파일 이름은 AWS 서비스 이름이다. 각 파일 맨 위 주석에 그 서비스가 무엇이고 왜 필요한지 적어 두었다. 파일을 나눈 것일 뿐 Terraform은 한 폴더의 `.tf`를 합쳐서 읽으므로 동작은 같다.

| 파일 | AWS 서비스 | 내용 |
|---|---|---|
| `vpc.tf` | VPC | VPC, 인터넷 게이트웨이, 퍼블릭·프라이빗 서브넷(AZ 2~3개), 퍼블릭 라우트 테이블 |
| `ec2_nat.tf` | EC2 | NAT 인스턴스(AZ마다 또는 1대), Elastic IP, 자동 복구 알람, 프라이빗 라우트 테이블과 NAT 경로 |
| `security_groups.tf` | VPC 보안 그룹 | ALB, NAT, DB, DB 작업용 방화벽. 앱 태스크용은 배포 모듈이 앱마다 만든다 |
| `alb.tf` | ELB | 공유 ALB, 80번 기본 리스너. 인증서를 주면 443 리스너와 80→443 리다이렉트 |
| `ecs.tf` | ECS | ECS 클러스터 |
| `ecr.tf` | ECR | 이미지 저장소와 수명 주기 정책 |
| `iam.tf` | IAM | 태스크 실행 역할, DB 접속 정보 읽기 권한(공유 URL, 관리자 비밀번호, `/apps/*`) |
| `rds.tf` | RDS | MySQL, DB 서브넷 그룹, 비밀번호 생성, 백업과 최종 스냅샷 설정 |
| `ssm.tf` | SSM | 공유 `DATABASE_URL`과 DB 관리자 비밀번호 SecureString 파라미터 |
| `db_provisioner.tf` | CloudWatch Logs | 앱별 DB를 만드는 1회성 작업의 로그 그룹 |
| `variables.tf`, `outputs.tf`, `versions.tf` | — | 입력 변수, 배포에 넘길 출력(`deploy_inputs`), provider 버전 |

## 입력 계약

배포 루트는 입력 파일 두 개만 받는다. LLM은 HCL을 만들지 않고 `app` 값만 낸다.

| 파일 | 작성 주체 | 내용 |
|---|---|---|
| `platform.auto.tfvars.json` | 플랫폼 코드 | `deploy_id`, `image`, `cpu_architecture`, `listener_port`, `database_url_parameter_arn`(앱 전용 DB 접속 정보), `health_check_grace_seconds`, `foundation`(foundation 출력 그대로) |
| `app.auto.tfvars.json` | 스키마 검증과 사용자 승인을 통과한 LLM 출력 | `container_port`, `health_check_path`, `task_size`, `min_tasks`, `max_tasks`, `use_database`, `environment` |

검증은 두 겹이다. 1차는 LLM 계층의 JSON 스키마, 2차는 Terraform 변수 검증과 사전 조건이다. JSON 스키마로 표현할 수 없는 `max_tasks >= min_tasks`는 LLM 계층 코드와 Terraform 사전 조건에서 확인한다.

태스크 크기 프리셋 (6단계 비용 계산 코드도 같은 값을 쓴다):

| task_size | vCPU | 메모리 |
|---|---|---|
| xsmall | 0.25 | 0.5 GB |
| small | 0.5 | 1 GB |
| medium | 1 | 2 GB |

## 최초 1회

리전은 계정에 지정된 곳을 **반드시 직접 지정한다**(기본값이 없다). 아래는 `sa-east-1` 기준이다.

```bash
# 1) state 버킷 (한 번만. 이 스택의 state는 로컬 파일이라 담당자가 보관한다)
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply -var="region=sa-east-1"
export PAVED_STATE_BUCKET=$(terraform -chdir=infra/bootstrap output -raw bucket)
export AWS_REGION=sa-east-1

# 2) foundation. 앱을 프라이빗 서브넷에 두는 실제 배포 구성은 enable_nat_instance=true
terraform -chdir=infra/foundation init
bash infra/scripts/deploy.sh foundation-state        # foundation state를 S3로 (선택, 권장)
terraform -chdir=infra/foundation apply -var="region=sa-east-1" -var="enable_nat_instance=true" \
  -var="certificate_arn=<ACM 인증서 ARN>"             # 인증서가 없으면 이 줄을 뺀다(HTTP만 열림, 시험용)
terraform -chdir=infra/foundation output -json deploy_inputs > infra/deployments/foundation.json
```

`deployments/_template/`에서도 `terraform init -backend=false`를 한 번 실행하고 생성된 `.terraform.lock.hcl`을 커밋한다. 배포 디렉터리가 같은 provider 버전을 쓰게 하기 위해서다.

### foundation 선택 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `certificate_arn` | 없음(HTTP만) | ACM 인증서. 지정하면 443 리스너, 80→443 리다이렉트, 배포별 리스너가 HTTPS가 된다. **실제 서비스는 반드시 지정한다** |
| `enable_nat_instance` | `false` | `true`면 앱을 프라이빗 서브넷 + NAT 인스턴스로 실행한다 |
| `nat_instance_type` | `t4g.small` | Graviton이어야 하고 small 이상만 허용한다(아래 NAT 참고) |
| `nat_high_availability` | `true` | `false`면 NAT 1대로 전체가 쓴다(비용 절감, 그 AZ가 멈추면 외부 통신 전체가 끊긴다) |
| `nat_ami_id` | 비움(최신 AMI) | NAT AMI를 고정한다 |
| `az_count` | `3` | 사용할 가용 영역 수(2~3) |
| `db_backup_retention_days` | `7` | RDS 자동 백업 보관 기간 |
| `db_multi_az` | `false` | 다중 AZ(비용 약 2배) |
| `final_snapshot` | `true` | RDS를 지울 때 최종 스냅샷을 남긴다 |
| `protect_from_destroy` | `true` | RDS 삭제 보호, ECR 강제 삭제 방지 |

## 배포 1건 (플랫폼 자동화 대상)

```bash
# provider를 배포마다 다시 내려받지 않도록 캐시 사용 (데모 시간 단축)
export TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"
mkdir -p "$TF_PLUGIN_CACHE_DIR"

ID=a1b2c3d4
D=infra/deployments/$ID
mkdir -p "$D"
# _template의 .terraform/ 폴더(provider 바이너리)는 복사하지 않는다
tar -C infra/deployments/_template --exclude=.terraform -cf - . | tar -C "$D" -xf -

# 이미지 빌드·푸시. 태그는 배포 ID + 리비전, latest 금지
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin <계정ID>.dkr.ecr.$AWS_REGION.amazonaws.com
docker buildx build --platform linux/amd64 -t <ecr_repository_url>:$ID-r1 --push .

# platform.auto.tfvars.json, app.auto.tfvars.json 작성 후
terraform -chdir="$D" init -input=false
terraform -chdir="$D" plan -input=false -out=tfplan        # 7단계
terraform -chdir="$D" show -json tfplan > "$D/plan.json"   # 8단계 승인 화면 데이터
terraform -chdir="$D" apply -input=false tfplan            # 9단계, 승인 후에만
terraform -chdir="$D" output -json                         # url, service_name, target_group_arn, log_group_name
```

10단계 헬스체크는 Terraform이 기다리지 않는다(`wait_for_steady_state = false`). 플랫폼이 타임아웃을 두고 아래를 조회한다.

```bash
aws elbv2 describe-target-health --target-group-arn <target_group_arn>
aws ecs describe-services --cluster paved-clouds --services <service_name>
aws logs tail <log_group_name> --since 10m    # 실패 분석 LLM 입력
```

## 배포 스크립트

위 "배포 1건" 절차를 `scripts/deploy.sh`로 묶었다. `bash`, `terraform`, `aws` CLI, `python`이 필요하고, 앱별 DB를 처음 만들 때는 `docker`가 한 번 필요하다. 리전은 `AWS_REGION`(없으면 `aws configure` 값)을 쓴다.

```bash
export AWS_REGION=sa-east-1
bash infra/scripts/deploy.sh up       --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --app app.json --plan-only   # 포트는 자동 할당
bash infra/scripts/deploy.sh apply    a1b2c3d4     # 위에서 만든 승인된 저장 계획만 적용한다
bash infra/scripts/deploy.sh update   a1b2c3d4 --image <ecr_url>:a1b2c3d4-r2 [--app app.json] --plan-only
bash infra/scripts/deploy.sh rollback a1b2c3d4 --plan-only   # 직전 정상 이미지로 되돌린다
bash infra/scripts/deploy.sh status   a1b2c3d4
bash infra/scripts/deploy.sh diagnose a1b2c3d4     # 실패 분석 정보(JSON). 비밀은 마스킹된다
bash infra/scripts/deploy.sh db-check a1b2c3d4     # 앱 전용 DB 계정이 자기 DB만 쓸 수 있는지 확인
bash infra/scripts/deploy.sh destroy  a1b2c3d4     # 앱 삭제. 앱 전용 DB는 보존한다
bash infra/scripts/deploy.sh drop-db  a1b2c3d4     # 앱 전용 DB와 계정을 지운다(되돌릴 수 없다)
```

`up` 옵션: `--port 8001|auto`(기본 auto), `--shared-db`(앱 전용 DB를 쓰지 않음), `--grace 초`(헬스체크 유예), `--skip-nat-check`, `--arch ARM64`, `--yes`, `--plan-only`.

- `app.json`은 `app.auto.tfvars.json`의 `app` 값만 담는다(예: `{"container_port": 8000, "health_check_path": "/health", "use_database": true, ...}`).
- `--plan-only`는 계획을 `plan.json`으로 저장하고 멈춘다. 승인 후 `apply`가 **저장된 계획만** 실행한다. 승인 뒤에 새 계획을 만들지 않는다. `--plan-only` 없이 실행하면 계획을 보여 주고 `yes`를 입력받는다.
- **리스너 포트**는 생략하면 공유 ALB에서 이미 쓰는 포트를 조회해 허용 범위(8001~8049)의 가장 작은 빈 포트를 고른다. 두 배포를 동시에 만들면 같은 포트를 고를 수 있고, 그때는 늦게 apply한 쪽이 실패한다.
- **헬스체크 대기**: ECS 배포가 `COMPLETED`이고 대상이 `healthy`일 때 통과한다. 대상만 보면 롤링 교체 중 옛 태스크 때문에 일찍 통과한다. 다음은 실패로 끝내고 이력에 기록하지 않는다.
  - 헬스체크가 4xx(예: 경로 오류 404)를 4번 연속 돌려줄 때. 기다려도 나아지지 않는 경우다. 실측으로 `apply` 후 183초에 실패가 확정됐다(태스크 기동과 대상 등록 약 1분 + 15초 간격 4회 확인). 5xx는 시작 중에 503을 주는 앱이 있어서 제외했다.
  - **이번 배포의** 태스크가 3개 이상 반복해서 죽을 때(현재 PRIMARY 배포가 시작한 태스크만 센다. 서비스 전체의 정지 태스크를 세면 이전 업데이트에서 교체된 태스크가 최근 1시간 동안 남아 있어서 업데이트를 몇 번 한 뒤의 정상 배포를 오판한다), 또는 대기 시간(`HEALTH_TIMEOUT`, 기본 300초)을 넘길 때
  - ECS 서킷 브레이커가 배포를 `FAILED`로 만들었을 때(태스크 3회 실패)
- **서킷 브레이커**: 실패가 계속되면 ECS가 끝없이 재시도하는 것을 막는다. 이전 정상 버전으로의 자동 복구(`rollback`)는 범위가 확정되기 전이라 켜지 않았다. 태스크가 3번 실패해야 `FAILED`가 되고, **실측으로 `apply` 후 약 8~9분이 걸린다**(헬스체크 유예 90초에서 527초, 30초에서 500초). 유예를 줄여도 거의 빨라지지 않는다. 태스크 하나가 실패하는 데 약 2분 15초가 걸리기 때문이다(기동, 대상 등록, 헬스체크 3회 실패, 교체). 그래서 사용자에게 "실패했다"고 알리는 것은 서킷 브레이커가 아니라 위의 4xx 즉시 판정이나 시간 초과가 먼저 하고, 서킷 브레이커는 그 뒤에 재시도를 멈추는 안전장치다. `--grace`는 실패를 빨리 확정하는 용도가 아니라 느리게 뜨는 앱(JVM 등)의 오탐을 막으려고 **늘리는** 용도다.
- **기록은 두 가지**다.
  - `deployments/<id>/history.log`: 헬스체크를 **통과한** 배포만. 한 줄이 `시각|이미지|스냅샷 이름`이고 롤백이 여기서 대상을 고른다. 스냅샷(`history/<이름>.app.json`, `.platform.json`)에는 그때의 **앱 설정 전체와 이미지·아키텍처·유예 시간**이 들어 있다.
  - `deployments/<id>/attempts.log`: 성공·**실패를 가리지 않는 모든 시도**. 한 줄이 `시각|결과(ok/fail)|이미지|사유`이고 실패 사유(시간 초과, 4xx, 서킷 브레이커, 태스크 반복 종료, `terraform apply` 실패, 앱 전용 DB 준비 실패)가 남는다. `status`가 최근 5건을 보여 주고 `diagnose` JSON에도 들어간다.
- **롤백**은 이력에서 **지금 배포된 것(Terraform 상태 기준)과 다른 가장 최근의 정상 이미지**를 고르고, 그 이미지가 정상이던 때의 **앱 설정도 함께** 복원한다. 그래서 업데이트가 실패했을 때도 마지막 정상 이미지로 돌아가고(이력의 마지막 줄로 "현재"를 정하면 실패한 업데이트 뒤에 이 이미지를 빼 버린다), `update --app`으로 포트·헬스체크 경로·크기·환경 변수를 바꿨어도 옛 이미지에 새 설정이 붙어 또 실패하지 않는다. foundation 값은 스냅샷이 아니라 지금 것을 쓴다. 스냅샷이 없는 옛 이력은 이미지만 되돌리고 경고한다. **사람이 명령으로 실행하며, 자동 복구는 만들지 않았다.**
- 롤백은 DB 스키마와 데이터를 되돌리지 않는다.
- `diagnose`는 ECS 배포 상태와 실패 사유, 대상 헬스 사유, 서비스 이벤트, 중단된 태스크 사유, 최근 로그를 JSON으로 낸다. 접속 URL의 계정·비밀번호, AWS 키, `password`·`token`·`secret` 값은 가린다. 정규식 기반이라 모든 형태의 비밀을 보장하지는 않는다.
- **NAT 점검**: NAT를 쓰는 foundation이면 `up` 전에 NAT 인스턴스의 부팅 로그로 초기화 성공을 확인한다. 로그가 없으면 90초 기다린 뒤 실패로 본다(1시간 넘게 실행된 인스턴스는 로그가 밀려났을 수 있어 경고만 한다).

### 앱별 DB

기본으로 앱이 DB를 쓰면(`use_database: true`) 배포마다 **전용 DB `app_<id>`와 그 DB에만 권한이 있는 계정**을 만든다. 모든 앱이 DB 관리자 계정으로 같은 DB를 쓰면 앱 하나가 뚫렸을 때 다른 앱의 데이터까지 읽고 지울 수 있기 때문이다.

- 계획 단계(`--plan-only`)에서는 접속 정보 ARN만 쓰고 아무것도 만들지 않는다. **승인 후 `apply`에서** SSM 파라미터(`/<프로젝트>/apps/<id>/database-url`)와 DB·계정을 만든다.
- DB는 프라이빗 서브넷에 있어서 VPC 안에서 `mysql` 클라이언트를 한 번 실행하는 Fargate 작업으로 만든다. 그 클라이언트 이미지는 처음에 한 번 ECR에 올린다(`docker` 필요).
- `destroy`는 앱만 지우고 **앱 전용 DB와 접속 정보는 보존한다**(데이터 보호). 지우려면 `drop-db` 또는 `destroy --drop-db`.
- `db-check`는 앱 전용 계정으로 접속해 자기 DB는 되고, 공유 DB(`app`)와 시스템 테이블은 막혀 있고, `SHOW DATABASES`에 다른 앱의 DB가 보이지 않는지 확인한다.
- `--shared-db`를 주면 공유 DB를 관리자 계정으로 쓴다(이전 방식). 공유 DB의 테이블 충돌 문제가 그대로 있다. 이 선택은 배포에 기록되어(`db-shared`) 나중에 `update`가 몰래 앱 전용 DB로 바꾸지 않는다.
- **DB 없이 만든 배포를 `update`로 `use_database: true`로 바꾸면** 앱 전용 DB로 전환한다(승인 후 `apply`에서 DB와 접속 정보를 만든다). 전환하지 않으면 모듈이 foundation의 공유 URL(DB 관리자 계정)로 대체해서, 앱 전용 DB가 기본이라는 약속과 달리 조용히 모든 DB에 대한 관리자 권한이 붙는다.
- 테이블 생성 주체(앱이 시작할 때 만드는지, 별도 마이그레이션인지)는 아직 팀이 정하지 않았다(`docs/OPEN_QUESTIONS.md`). 앱 전용 DB는 빈 상태로 만들어지므로 지금은 앱이 스스로 만드는 방식(`sample-back`)과 맞는다.

### 원격 state

로컬 state 파일은 잃어버리면 AWS 리소스를 지울 수 없고, DB 비밀번호가 평문으로 들어 있다. `PAVED_STATE_BUCKET`을 지정하면 배포 state를 S3(`deployments/<id>/terraform.tfstate`)에 두고, `deploy.sh foundation-state`로 foundation state도 옮긴다. 버전 관리(실수로 덮어써도 되돌림), 암호화, 잠금(`use_lockfile`, 동시 실행 방지)을 쓴다. 환경마다 다른 `backend_override.tf`는 Git에서 제외된다. 지정하지 않으면 로컬 파일을 쓴다.

배포 디렉터리(`deployments/<id>/`)는 입력 파일(`*.auto.tfvars.json`)과 이력이 든 곳이라 state가 S3에 있어도 플랫폼 서버에 남겨 둔다. 입력 파일은 state에 없어서, 디렉터리를 잃으면 같은 입력으로 다시 만들어야 `destroy`할 수 있다.

## 롤백

`deploy.sh rollback`이 이력에서 대상을 골라 그 이미지와 정상이던 때의 앱 설정을 `platform.auto.tfvars.json`·`app.auto.tfvars.json`에 되돌리고 다시 plan·apply 한다(위 "배포 스크립트" 참고). 태스크 정의만 바뀌고 서비스가 이전 이미지로 교체된다. ECR 태그는 덮어쓸 수 없게 설정되어 있어 같은 태그는 항상 같은 이미지다.

## 정리

```bash
bash infra/scripts/deploy.sh destroy <deploy_id>                  # 배포 1건 삭제(앱 전용 DB는 보존)
bash infra/scripts/deploy.sh drop-db <deploy_id>                  # 앱 전용 DB까지 지울 때
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=paved-clouds Key=ManagedBy,Values=paved-clouds-platform   # 남은 배포 리소스 확인
```

foundation까지 지울 때는 RDS 삭제 보호 때문에 순서가 있다. 시험용이라 최종 스냅샷이 필요 없으면 `final_snapshot=false`를 같이 준다.

```bash
terraform -chdir=infra/foundation apply   -var="region=sa-east-1" -var="enable_nat_instance=true" -var="protect_from_destroy=false" -var="final_snapshot=false"
terraform -chdir=infra/foundation destroy -var="region=sa-east-1" -var="enable_nat_instance=true" -var="protect_from_destroy=false" -var="final_snapshot=false"
```

## 주의점

- Apple Silicon에서 그냥 빌드한 이미지는 ARM64다. `--platform linux/amd64`로 빌드하거나 `cpu_architecture`를 `ARM64`로 넘긴다. 둘이 다르면 태스크가 바로 종료된다.
- `container_port`나 `deploy_id`를 바꾸면 대상 그룹 이름이 충돌한다. 새 배포 ID로 배포한다. `deploy_id`는 소문자·숫자 4~8자다(하이픈 불가).
- 앱 URL은 `<http|https>://<ALB 주소>:<8001~8049 포트>` 형식이다. ALB당 리스너 할당량 50개 중 80번(HTTPS면 443도)이 쓰고 남은 만큼만 동시에 유지할 수 있다(기본 설정에서 49개). 행사장 네트워크가 8000번대 포트를 막을 수 있으니 리허설 때 휴대폰 데이터와 행사장 Wi-Fi 양쪽에서 접속해 본다.
- **HTTPS**: 인증서를 주지 않으면 비밀번호와 로그인 쿠키가 평문으로 오간다. 시험용으로만 쓴다. HTTPS를 켜면 앱의 `COOKIE_SECURE`도 `true`로 넘겨야 쿠키에 `Secure` 속성이 붙는다(예: `sample-back`). 반대로 HTTP로 `COOKIE_SECURE=true`를 주면 로그인이 되지 않는다. 도메인이 생기면 80/443 리스너의 호스트 헤더 라우팅으로 바꾸는 것이 정석이다.
- DB 비밀번호는 foundation state에 남는다. 로컬 state를 쓰면 Git에 올리지 않고 담당자만 보관한다. S3 state를 쓰면 버킷 접근 권한을 담당자로 제한한다.
- RDS는 MySQL 8.4다. 8.0은 2026-07-31 표준 지원이 끝나 유료 Extended Support 대상이라 막아 두었다.
- **보안 그룹**: 앱 태스크는 앱마다 전용 보안 그룹을 받고 ALB가 보내는 컨테이너 포트 하나만 허용한다. 그래서 앱끼리 서로 접근할 수 없고, DB를 쓰지 않는 앱은 DB에 닿을 수 없다. 앱 아웃바운드는 전체 허용이다(ECR, 로그, 앱이 부르는 외부 API). ALB의 8001~8049 포트는 의도적으로 전 세계에 열려 있다(공개 앱).
- **RDS 보호**: 자동 백업 7일(기본), 삭제할 때 최종 스냅샷(기본), 삭제 보호(기본). 다중 AZ는 `db_multi_az`로 켠다. 교육용·무료 계정은 백업 보관 기간이나 다중 AZ를 제한할 수 있어서 그때는 변수로 낮춘다.
- `enable_nat_instance = false`(기본)면 앱 태스크가 퍼블릭 서브넷에서 퍼블릭 IP로 실행된다. 앱 전용 보안 그룹이 ALB가 보내는 포트만 허용한다.
- `enable_nat_instance = true`면 NAT Gateway 대신 EC2 NAT 인스턴스(`nat_instance_type`, 기본 `t4g.small`, Amazon Linux 2023 arm64)와 Elastic IP를 만든다. 고가용성(기본)이면 AZ마다 1대씩이고 프라이빗 라우트 테이블도 AZ별로 나뉘어 AZ 하나가 멈춰도 다른 AZ의 외부 통신(ECR pull·로그 전송·SSM 조회)을 유지한다. `nat_high_availability=false`면 1대를 모두가 쓴다.
- NAT 인스턴스는 메모리 512MB인 `t4g.nano`로는 부팅할 때 `dnf install iptables-services`가 메모리 부족으로 종료되어 NAT가 동작하지 않았다(상태 검사는 정상으로 나온다). 그래서 기본 타입이 `t4g.small`이고 nano·micro는 변수 검증이 막는다. 타입만 바꾸면 초기화 스크립트가 다시 실행되지 않으므로 인스턴스를 교체(`-replace`)해야 한다.
- NAT AMI는 만든 뒤 바뀌어도 인스턴스를 교체하지 않는다(`ignore_changes = [ami]`). Amazon Linux 2023 최신 AMI 파라미터가 자주 갱신되는데(190번 넘게), 무시하지 않으면 `plan`이 NAT 교체를 제안하고 교체 중 외부 통신이 끊긴다. 최신 AMI로 바꿀 때는 `terraform apply -replace='aws_instance.nat[0]'`처럼 하나씩 교체한다. 재현 가능한 배포를 원하면 `nat_ami_id`로 고정한다.
- NAT 인스턴스의 시스템 상태 검사가 실패하면 CloudWatch 알람이 EC2 auto recovery를 실행한다. 인스턴스 안의 OS나 `iptables` 문제는 자동 복구되지 않는다.

## 검증 범위

### 회귀 시험 (AWS 리소스를 만들지 않음)

`python infra/scripts/test_infra.py` (자격증명이 없으면 `--skip-aws`)

- A. 배포 모듈 입력 검증: 잘못된 값이 Terraform 단계에서 막히는지, 정상 값이 통과하는지
- B. 배포 모듈 계획 내용: 앱 전용 보안 그룹, 서킷 브레이커, HTTPS 리스너, 앱별 DB 접속 정보
- C. foundation 변수 검증과 계획 내용(NAT 수, AZ 수, AMI 고정, HTTPS, RDS 옵션). 계획 내용은 AWS 조회가 필요하다
- D. `deploy.sh` 함수: `deploy_id` 규칙, 앱 전용 DB 이름·ARN, 비밀 마스킹, DB 작업 정의(로그 그룹·ARN·이미지가 입력과 같은지, 평문 비밀번호가 없는지), state 설정, NAT 부팅 로그 판정
- E. 정적 검사: `terraform fmt`, `validate`, `bash -n`

판정은 Terraform이 실제로 내는 메시지로만 한다. 시험 환경이 고장난 것(초기화 실패 등)은 "차단 성공"이 아니라 **"시험환경오류"로 따로 센다**. 이 구분이 없으면 환경 오류가 전부 성공으로 읽힌다. 시험이 실제 실행과 같은 조건(Git Bash의 `/c/...` 경로)에서 도는지도 중요해서, 아래 AWS 시험에서 이 차이로 놓친 버그가 둘 있었다(고쳐서 시험에 반영했다).

### AWS 시험

기준: 2026-10-09, 리전 `sa-east-1`(상파울루), HashiCorp Terraform 1.16.5, AWS provider 6.x, 로컬 Docker Desktop. 실제 적용은 담당자의 실행 요청 범위에서 했고, 매 `apply` 전에 계획을 확인했다.

**확인한 것**

- foundation 새 구조로 생성(59개, 393초, RDS 6분 5초), S3 state 버킷(6개) 생성. RDS 자동 백업 7일 설정이 교육용 계정에서 허용됨.
- **HTTPS**: 자체 서명 인증서를 ACM에 가져와 시험. 443 리스너 TLSv1.3, 80→443 리다이렉트(301), 443 기본은 404, 배포별 리스너(8001~)는 HTTPS. 앱이 `COOKIE_SECURE=true`일 때 로그인 쿠키에 `Secure` 속성이 붙고 가입·로그인·글 작성·투표가 정상.
- **앱별 DB**: 배포마다 DB와 전용 계정이 자동 생성되고(`PROVISION_OK`) 앱이 그 계정으로 동작. `db-check`로 자기 DB만 접근, 공유 DB(`app`)는 `Access denied`(1044), `mysql.user`는 `SELECT denied`(1142), `SHOW DATABASES`에 다른 앱 DB가 보이지 않음을 확인. 앱 2개(`sbtest`, `sbtwo`)를 동시에 띄워 데이터가 분리됨(같은 이메일이 양쪽에 가입 가능).
- **앱별 보안 그룹**: 앱마다 태스크 SG가 따로 있고 인바운드는 ALB SG에서 오는 컨테이너 포트 하나뿐(IP 대역 허용 0). ALB·DB 쪽 규칙이 앱 수만큼 늘어남.
- **포트 자동 할당**: 8001, 8002, 8003을 순서대로 받음(이미 쓰는 포트를 건너뜀).
- **S3 state**: foundation과 배포 state가 버킷에 저장되고 로컬 state 파일이 없음.
- **AMI 고정·무시**: 현재 NAT와 다른(더 오래된) AMI를 `nat_ami_id`로 주고 `plan`을 돌리면 "No changes". `-replace`로 하나만 교체할 때만 AMI가 바뀐다. (`ignore_changes = [ami]`가 없었다면 NAT 3대 교체가 제안되는 상황)
- **NAT 점검**: 새 NAT 3대에서 초기화 성공을 부팅 로그로 확인. 실패 판정 로직은 `t4g.nano` 장애 때의 실제 로그 문장으로 시험(고장난 NAT를 실제로 만들지는 못했다. nano를 변수 검증이 막기 때문).
- **실패 배포**(헬스체크 경로 오류 404): 스크립트의 4xx 즉시 판정이 `apply` 후 183초에 실패를 확정하고 이력에 기록하지 않음. ECS 서킷 브레이커는 500초(8분 20초)에 배포를 `FAILED`로 만듦(유예 90초에서 527초였으니 유예는 거의 영향이 없다). `diagnose`가 배포 `FAILED` 상태·사유, 대상 헬스 404, 중단된 태스크, 로그를 수집.
- **업데이트·롤백**(새 구조에서): 이미지 교체와 직전 정상 이미지로의 복귀가 무중단(교체 구간 1초 간격 감시에서 `/health`와 로그인 쿠키 모두 끊김 0회, 약 3.5분). 태스크 2개에 요청이 분산되고 어느 태스크에서나 로그인이 유지된다. 롤백 후 `db-check` 통과.
- **삭제**: `destroy`는 앱 전용 DB를 보존(앱 없이 `db-check` 통과), `drop-db`는 DB와 접속 정보 파라미터를 지움.
- 이전 구조에서 확인한 것(2026-10-08): foundation 생성·삭제, 앱 배포 4건, 프라이빗 서브넷 태스크의 NAT·SSM·RDS 경로, 세션이 DB에 있어 교체 후에도 로그인 유지.

**시험 중 발견해 고친 문제**

- `t4g.nano` NAT 초기화 실패(메모리 부족, 상태 검사는 정상으로 나옴) → 기본 `t4g.small`, nano·micro는 변수 검증이 차단.
- 롤링 교체 중 옛 태스크 때문에 일찍 "정상"으로 판정 → ECS 배포 `COMPLETED`까지 대기.
- Git Bash가 `/`로 시작하는 환경 변수 값(로그 그룹 이름)을 Windows 경로로 바꿔 DB 작업 정의 등록이 실패 → 변환을 끄되 그 호출에만 적용. 이 수정이 같은 줄의 `$(...)`까지 영향을 줘서 한 번 더 실패 → 값을 먼저 읽도록 수정.
- `db-check`가 MySQL 시스템 스키마 `performance_schema`를 다른 앱 DB로 오인 → 시스템 스키마 허용.
- 서킷 브레이커 판정 시간을 잘못 예상(공식으로는 약 3.5분, 실측 8분대) → 문서를 실측으로 정정.
- 회귀 시험이 위 버그들을 놓친 이유(로그 그룹 값을 확인하지 않음, 경로 형태가 실제와 다름, 시험 복사에 `backend_override.tf`가 섞임)를 고쳐 시험에 반영. 보강한 시험이 수정을 뺀 상태에서는 실제로 실패하는 것까지 확인.

**검증 안 됨**

- 실제 도메인 인증서(ACM DNS 검증)와 도메인 기반 라우팅. 자체 서명 인증서로만 시험했다.
- 다른 PC에서 S3 state로 이어서 `destroy`(한 PC에서만 시험).
- `db_multi_az`, `final_snapshot=true`로 삭제(스냅샷 남기기), `nat_high_availability=false`, `az_count=2`의 **실제 생성**(계획 내용만 회귀 시험으로 확인).
- 두 배포를 정확히 동시에 `up`할 때의 포트 경합(문서에 한계를 적었다).
- CPU 기반 오토스케일링(`max_tasks > min_tasks`)의 실제 증감, 부하 상황.
- `cpu_architecture = ARM64` 이미지.
- 이미지 빌드·푸시를 포함한 전체 배포 시간과 3분 데모 목표 충족 여부. 이미지가 이미 있을 때 `apply`~`healthy`는 약 2~3분(앱 전용 DB 준비 포함 약 3분)이었다.
- 테이블 생성 주체(앱 시작 시 자동 생성 vs 마이그레이션)는 팀 미결(`docs/OPEN_QUESTIONS.md`). 앱 전용 DB는 빈 DB로 만들어진다.
- 플랫폼 백엔드와의 연동. 프런트의 승인 화면은 아직 `plan.json`을 읽지 않는다.
- 행사장 네트워크에서 8001번대 포트 접속.
