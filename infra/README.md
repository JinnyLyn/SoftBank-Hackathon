# infra

Paved Clouds의 AWS 배포 계층이다. 담당: 이태훈

## 구조

| 경로 | 역할 | 누가 실행하나 |
|---|---|---|
| `foundation/` | 사전 생성 리소스: VPC, 서브넷, 보안 그룹, 공유 ALB, ECS 클러스터, ECR, 태스크 실행 역할, RDS MySQL, `DATABASE_URL` 파라미터 | 담당자가 행사 전에 1회 apply. 플랫폼은 건드리지 않는다 |
| `modules/ecs-web-app/` | 검증된 모듈. 배포 1건 = 로그 그룹, 태스크 정의, 대상 그룹, 전용 포트 리스너, ECS 서비스, 오토스케일링 | 배포 루트가 호출 |
| `modules/ecs-web-app/app-config.schema.json` | LLM 출력이 따라야 할 JSON 스키마. `variables.tf`의 LLM 입력과 규칙이 같다 | LLM 계층이 3·5단계 출력 검증에 사용 |
| `deployments/_template/` | 배포 건별 루트 모듈 템플릿 | 플랫폼이 `deployments/<deploy_id>/`로 복사해서 사용 |
| `scripts/deploy.sh` | 배포 1건을 만들고(`up`) 바꾸고(`update`) 되돌리고(`rollback`) 지우는(`destroy`) 자동화. 실패 분석 정보 수집(`diagnose`) 포함 | 플랫폼 백엔드 또는 담당자가 호출. 아래 "배포 스크립트" 참고 |

### modules/ecs-web-app 파일 안내 (AWS 서비스별)

| 파일 | AWS 서비스 | 내용 |
|---|---|---|
| `main.tf` | — | 모듈 개요, 리전 조회, 태스크 크기 프리셋 등 공용 `locals` |
| `cloudwatch.tf` | CloudWatch Logs | 배포 건별 로그 그룹 |
| `ecs.tf` | ECS Fargate | 태스크 정의(컨테이너 실행 명세), 서비스 |
| `alb.tf` | ELB | 대상 그룹, 이 배포 전용 포트 리스너 |
| `autoscaling.tf` | Application Auto Scaling | 태스크 수 범위, CPU 기준 자동 증감 |
| `variables.tf`, `outputs.tf`, `versions.tf`, `app-config.schema.json` | — | 입력 변수, 출력값, provider 버전, LLM 출력 스키마 |

### foundation 파일 안내 (AWS 서비스별)

파일 이름은 AWS 서비스 이름이다. 각 파일 맨 위 주석에 그 서비스가 무엇이고 왜 필요한지 적어 두었다. 파일을 나눈 것일 뿐 Terraform은 한 폴더의 `.tf`를 합쳐서 읽으므로 동작은 같다.

| 파일 | AWS 서비스 | 내용 |
|---|---|---|
| `vpc.tf` | VPC | VPC, 인터넷 게이트웨이, 퍼블릭·프라이빗 서브넷(AZ 3개), 퍼블릭 라우트 테이블 |
| `ec2_nat.tf` | EC2 | AZ별 NAT 인스턴스, Elastic IP, 자동 복구 알람, 프라이빗 라우트 테이블과 NAT 경로 |
| `security_groups.tf` | VPC 보안 그룹 | ALB, 앱 태스크, NAT, DB 방화벽 규칙 |
| `alb.tf` | ELB | 공유 ALB, 80번 기본 리스너 |
| `ecs.tf` | ECS | ECS 클러스터 |
| `ecr.tf` | ECR | 이미지 저장소와 수명 주기 정책 |
| `iam.tf` | IAM | 태스크 실행 역할, `DATABASE_URL` 읽기 권한 |
| `rds.tf` | RDS | MySQL, DB 서브넷 그룹, 비밀번호 생성 |
| `ssm.tf` | SSM | `DATABASE_URL` SecureString 파라미터 |
| `variables.tf`, `outputs.tf`, `versions.tf` | — | 입력 변수, 배포에 넘길 출력(`deploy_inputs`), provider 버전 |

## 입력 계약

배포 루트는 입력 파일 두 개만 받는다. LLM은 HCL을 만들지 않고 `app` 값만 낸다.

| 파일 | 작성 주체 | 내용 |
|---|---|---|
| `platform.auto.tfvars.json` | 플랫폼 코드 | `deploy_id`, `image`, `cpu_architecture`, `listener_port`, `foundation`(foundation 출력 그대로) |
| `app.auto.tfvars.json` | 스키마 검증과 사용자 승인을 통과한 LLM 출력 | `container_port`, `health_check_path`, `task_size`, `min_tasks`, `max_tasks`, `use_database`, `environment` |

검증은 두 겹이다. 1차는 LLM 계층의 JSON 스키마, 2차는 Terraform 변수 검증과 사전 조건이다. JSON 스키마로 표현할 수 없는 `max_tasks >= min_tasks`는 LLM 계층 코드와 Terraform 사전 조건에서 확인한다.

태스크 크기 프리셋 (6단계 비용 계산 코드도 같은 값을 쓴다):

| task_size | vCPU | 메모리 |
|---|---|---|
| xsmall | 0.25 | 0.5 GB |
| small | 0.5 | 1 GB |
| medium | 1 | 2 GB |

## 최초 1회: foundation

```bash
cd infra/foundation
terraform init
# 리전은 계정에 지정된 곳을 쓴다. 기본값은 ap-northeast-2이므로 다른 리전이면 반드시 지정한다
# 앱을 프라이빗 서브넷에 두는 실제 배포 구성은 enable_nat_instance=true 이다 (기본값 false는 비용 절감용)
terraform apply -var="region=sa-east-1" -var="enable_nat_instance=true"
terraform output -json deploy_inputs > ../deployments/foundation.json
```

`deployments/_template/`에서도 `terraform init -backend=false`를 한 번 실행하고 생성된 `.terraform.lock.hcl`을 커밋한다. 배포 디렉터리가 같은 provider 버전을 쓰게 하기 위해서다.

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

위 "배포 1건" 절차를 `scripts/deploy.sh`로 묶었다. `bash`, `terraform`, `aws` CLI, `python`이 필요하다. 리전은 `AWS_REGION`(없으면 `aws configure` 값)을 쓴다.

```bash
export AWS_REGION=sa-east-1
bash infra/scripts/deploy.sh up       --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --port 8001 --app app.json --plan-only
bash infra/scripts/deploy.sh apply    a1b2c3d4     # 위에서 만든 승인된 저장 계획만 적용한다
bash infra/scripts/deploy.sh update   a1b2c3d4 --image <ecr_url>:a1b2c3d4-r2 [--app app.json] --plan-only
bash infra/scripts/deploy.sh rollback a1b2c3d4 --plan-only   # 직전 정상 이미지로 되돌린다
bash infra/scripts/deploy.sh status   a1b2c3d4
bash infra/scripts/deploy.sh diagnose a1b2c3d4     # 실패 분석 정보(JSON). 비밀은 마스킹된다
bash infra/scripts/deploy.sh destroy  a1b2c3d4
```

- `app.json`은 `app.auto.tfvars.json`의 `app` 값만 담는다(예: `{"container_port": 8000, "health_check_path": "/health", "use_database": true, ...}`).
- `--plan-only`는 계획을 `plan.json`으로 저장하고 멈춘다. 승인 후 `apply`가 **저장된 계획만** 실행한다. 승인 뒤에 새 계획을 만들지 않는다. `--plan-only` 없이 실행하면 계획을 보여 주고 `yes`를 입력받는다.
- 헬스체크는 ECS 배포가 `COMPLETED`가 되고 대상이 `healthy`일 때 통과로 본다. 대상만 보면 롤링 교체 중 옛 태스크 때문에 일찍 통과한다. 태스크가 반복 종료되거나 시간(`HEALTH_TIMEOUT`, 기본 300초)을 넘기면 실패로 끝나고 이력에 기록하지 않는다.
- 이력(`deployments/<id>/history.log`)에는 헬스체크를 통과한 이미지만 남는다. 롤백은 그 이력에서 현재와 다른 가장 최근 이미지를 고른다. **사람이 명령으로 실행하며, 자동 복구는 범위가 확정되지 않아 만들지 않았다.**
- 롤백은 이미지만 되돌린다. DB 스키마와 데이터는 되돌아가지 않는다.
- `diagnose`는 대상 헬스 사유, 서비스 이벤트, 중단된 태스크 사유, 최근 로그를 JSON으로 낸다. 접속 URL의 계정·비밀번호, AWS 키, `password`·`token`·`secret` 값은 가린다. 정규식 기반이라 모든 형태의 비밀을 보장하지는 않는다.

## 롤백

배포 이력의 직전 이미지를 `platform.auto.tfvars.json`의 `image`에 넣고 다시 plan·apply 한다. 태스크 정의만 바뀌고 서비스가 이전 이미지로 교체된다. ECR 태그는 덮어쓸 수 없게 설정되어 있어 같은 태그는 항상 같은 이미지다.

## 정리

```bash
terraform -chdir=infra/deployments/<deploy_id> destroy          # 배포 1건 삭제
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=paved-clouds Key=ManagedBy,Values=paved-clouds-platform   # 남은 배포 리소스 확인
```

행사 종료 후 foundation까지 지울 때만 `protect_from_destroy = false`로 apply한 다음 `terraform destroy`를 실행한다.

## 주의점

- Apple Silicon에서 그냥 빌드한 이미지는 ARM64다. `--platform linux/amd64`로 빌드하거나 `cpu_architecture`를 `ARM64`로 넘긴다. 둘이 다르면 태스크가 바로 종료된다.
- `container_port`나 `deploy_id`를 바꾸면 대상 그룹 이름이 충돌한다. 새 배포 ID로 배포한다.
- 앱 URL은 `http://<ALB 주소>:<8001~8049 포트>` 형식이다. ALB당 리스너 할당량 50개 중 80번이 1개를 써서 동시에 유지할 수 있는 배포는 49개다. 행사장 네트워크가 8000번대 포트를 막을 수 있으니 리허설 때 휴대폰 데이터와 행사장 Wi-Fi 양쪽에서 접속해 본다.
- HTTPS는 없다. 도메인이 생기면 80/443 리스너의 호스트 헤더 라우팅으로 바꾸는 것이 정석이다.
- DB 비밀번호는 foundation state에 남는다. foundation state는 Git에 올리지 않고 담당자만 보관한다.
- RDS는 MySQL 8.4다. 8.0은 2026-07-31 표준 지원이 끝나 유료 Extended Support 대상이라 막아 두었다.
- 모든 앱이 RDS의 `app` 데이터베이스 하나를 같이 쓴다. 앱 간 테이블 충돌과 테이블 생성 주체는 미결 사항(DB 마이그레이션)이다.
- `enable_nat_instance = false`(기본)면 앱 태스크가 퍼블릭 서브넷에서 퍼블릭 IP로 실행된다. 인바운드는 ALB 보안 그룹으로만 열려 있다.
- 앱 태스크를 프라이빗 서브넷으로 옮기려면 `enable_nat_instance = true`로 바꾼다. 이때 NAT Gateway 대신 AZ마다 EC2 NAT 인스턴스(`nat_instance_type`, 기본 `t4g.small`, Amazon Linux 2023 arm64)와 Elastic IP를 하나씩 만든다. 프라이빗 라우트 테이블도 AZ별로 나뉘어 각 서브넷은 같은 AZ의 NAT로만 나간다. AZ 하나가 멈춰도 다른 AZ의 태스크는 외부 통신(ECR pull·로그 전송·SSM 조회)을 유지한다.
- NAT 인스턴스는 메모리 512MB인 `t4g.nano`로는 부팅할 때 `dnf install iptables-services`가 메모리 부족으로 종료되어 NAT가 동작하지 않았다(상태 검사는 정상으로 나온다). 그래서 기본 타입이 `t4g.small`이다. 타입만 바꾸면 초기화 스크립트가 다시 실행되지 않으므로 인스턴스를 교체(`-replace`)해야 한다.
- NAT 인스턴스의 시스템 상태 검사가 실패하면 CloudWatch 알람이 EC2 auto recovery를 실행한다. 인스턴스 안의 OS나 `iptables` 문제는 자동 복구되지 않는다.

## 검증 범위

기준: 2026-10-08~09, 리전 `sa-east-1`(상파울루), HashiCorp Terraform 1.16.5, AWS provider 6.x, 로컬 Docker Desktop. 실제 AWS 적용은 담당자의 실행 요청 범위에서 했고, 시험 후 모두 삭제했다.

**확인한 것**

- `fmt`, `validate`(foundation, 템플릿). 이전 담당자가 OpenTofu 1.13.1로 한 정적 검사(잘못된 입력 11종 차단, JSON 스키마 검증)도 유효하다.
- foundation 생성 2회(54개 리소스, NAT 켠 구성, RDS 생성이 6분 20초대로 대부분). 삭제도 확인(삭제 보호 해제 후 `destroy`).
- 앱 배포 4건: 공개 nginx 이미지, `sample-app`(`DATABASE_URL`, `/health`가 DB까지 확인), `sample-back`(Docker로 빌드한 `Ophelia0419` 브랜치 이미지), 일부러 실패시킨 배포. 이미지가 ECR에 있을 때 `apply` 후 대상 `healthy`까지 약 30~40초.
- 프라이빗 서브넷의 Fargate 태스크가 NAT로 ECR에서 이미지를 받고, SSM의 `DATABASE_URL`을 secrets로 주입받아 RDS MySQL 8.4에 접속. 가입·로그인·글 작성·투표·로그아웃 정상.
- 태스크 2개: 서로 다른 서브넷의 태스크로 요청이 분산되고, 이미지 교체 전에 받은 로그인 쿠키가 교체 후 어느 태스크에서도 유효(세션이 DB에 있음).
- `update`·`rollback`: 이미지 교체와 직전 정상 이미지로의 복귀가 무중단으로 끝남(ECS 배포 완료까지 약 3분). 롤백 후에도 로그인 유지.
- 실패 배포(헬스체크 경로 오류)가 시간 초과로 실패 처리되고 이력에 기록되지 않음. `diagnose`가 `Target.ResponseCodeMismatch [404]`와 로그를 수집. 마스킹은 가짜 비밀 값 8종으로 시험.
- 발견해 고친 문제: `t4g.nano` NAT 초기화 실패(위 참고), 롤링 교체 중 일찍 "정상"으로 판정하던 스크립트 버그.

**검증 안 됨**

- HTTPS와 `COOKIE_SECURE=true` 동작(ALB가 HTTP만 있다). 도메인·인증서 결정 필요.
- 원격 state. 현재 foundation·배포 state는 로컬 파일이다.
- 서로 다른 앱이 같은 DB를 쓸 때의 테이블 충돌, DB 마이그레이션 주체(팀 미결, `docs/OPEN_QUESTIONS.md`). 시험은 앱마다 새 RDS로 했다.
- CPU 기반 오토스케일링(`max_tasks > min_tasks`)의 실제 증감, 부하 상황.
- `cpu_architecture = ARM64` 이미지.
- 이미지 빌드·푸시를 포함한 전체 배포 시간과 3분 데모 목표 충족 여부. 이미지가 이미 있을 때의 `apply`~`healthy`만 쟀다.
- 플랫폼 백엔드와의 연동. 프런트의 승인 화면은 아직 `plan.json`을 읽지 않는다.
- 행사장 네트워크에서 8001번대 포트 접속.
