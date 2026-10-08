# infra

Paved Clouds의 AWS 배포 계층이다. 담당: 이태훈

## 구조

| 경로 | 역할 | 누가 실행하나 |
|---|---|---|
| `foundation/` | 사전 생성 리소스: VPC, 서브넷, 보안 그룹, 공유 ALB, ECS 클러스터, ECR, 태스크 실행 역할, RDS MySQL, `DATABASE_URL` 파라미터 | 담당자가 행사 전에 1회 apply. 플랫폼은 건드리지 않는다 |
| `modules/ecs-web-app/` | 검증된 모듈. 배포 1건 = 로그 그룹, 태스크 정의, 대상 그룹, 전용 포트 리스너, ECS 서비스, 오토스케일링 | 배포 루트가 호출 |
| `modules/ecs-web-app/app-config.schema.json` | LLM 출력이 따라야 할 JSON 스키마. `variables.tf`의 LLM 입력과 규칙이 같다 | LLM 계층이 3·5단계 출력 검증에 사용 |
| `deployments/_template/` | 배포 건별 루트 모듈 템플릿 | 플랫폼이 `deployments/<deploy_id>/`로 복사해서 사용 |

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
terraform apply
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
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com
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
- NAT 인스턴스의 시스템 상태 검사가 실패하면 CloudWatch 알람이 EC2 auto recovery를 실행한다. 인스턴스 안의 OS나 `iptables` 문제는 자동 복구되지 않는다.

## 검증 범위

- 완료: OpenTofu 1.13.1 + AWS provider 6.67.0으로 `fmt`, `validate`(foundation, 템플릿), 예시 입력으로 템플릿 오프라인 `plan`(리소스 7개 생성 계획 확인), 잘못된 입력 11종 차단 확인, JSON 스키마 검증
- 검증 안 됨: 실제 AWS apply, foundation `plan`(AZ 조회에 AWS 접속 필요), HashiCorp Terraform CLI 실행, 배포 소요 시간
