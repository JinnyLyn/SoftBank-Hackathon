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
bash infra/scripts/deploy.sh foundation-state        # foundation state를 S3로 (선택, 권장. S3 state는 Terraform 1.10 이상)
terraform -chdir=infra/foundation apply -var="region=sa-east-1" -var="enable_nat_instance=true" \
  -var="certificate_arn=<ACM 인증서 ARN>"             # 인증서가 없으면 이 줄을 뺀다(HTTP만 열림, 시험용)
terraform -chdir=infra/foundation output -json deploy_inputs > infra/deployments/foundation.json
```

`deployments/_template/`에서도 `terraform init -backend=false`를 한 번 실행하고 생성된 `.terraform.lock.hcl`을 커밋한다. 배포 디렉터리가 같은 provider 버전을 쓰게 하기 위해서다.

### foundation 선택 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `certificate_arn` | 없음(HTTP만) | ACM 인증서. 지정하면 443 리스너, 80→443 리다이렉트, 배포별 리스너가 HTTPS가 된다. **실제 서비스는 반드시 지정한다** |
| `enable_nat_instance` | `true` | `true`면 앱을 프라이빗 서브넷 + NAT 인스턴스로 실행한다 |
| `enable_db_lambda` | `true` | 앱별 DB 준비·확인을 VPC 안의 Lambda로 한다(몇 초). `false`면 Lambda를 만들지 않고 Fargate 작업(약 70초 더)으로 대신한다 |
| `nat_instance_type` | `t4g.small` | Graviton이어야 하고 small 이상만 허용한다(아래 NAT 참고) |
| `nat_high_availability` | `true` | `false`면 NAT 1대로 전체가 쓴다(비용 절감, 그 AZ가 멈추면 외부 통신 전체가 끊긴다) |
| `nat_ami_id` | 비움(최신 AMI) | NAT AMI를 고정한다 |
| `az_count` | `3` | 사용할 가용 영역 수(2~3) |
| `db_backup_retention_days` | `7` | RDS 자동 백업 보관 기간 |
| `db_multi_az` | `false` | 다중 AZ(비용 약 2배) |
| `final_snapshot` | `true` | RDS를 지울 때 최종 스냅샷을 남긴다 |
| `protect_from_destroy` | `true` | RDS 삭제 보호, ECR 강제 삭제 방지 |
| `ecr_keep_images` | `200` | ECR에 보관할 최근 앱 이미지 수. **저장소 하나를 모든 앱이 같이 쓰므로** 앱 수 × 되돌릴 버전 수보다 커야 한다. 부족하면 오래된 앱의 롤백 대상 이미지가 지워진다. DB 작업용 `tools-` 이미지는 별도 규칙이라 밀려나지 않는다 |

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

위 "배포 1건" 절차를 `scripts/deploy.sh`로 묶었다. `bash`, `terraform`, `aws` CLI, `python`이 필요하고, `build`와 (Lambda가 없는 foundation에서) 앱별 DB 첫 생성에는 `docker`가 필요하다. 리전은 `AWS_REGION`(없으면 `aws configure` 값)을 쓴다.

```bash
export AWS_REGION=sa-east-1
bash infra/scripts/deploy.sh make-id  "프로젝트 이름"        # 이름(한글 포함)에서 배포 ID를 만든다. 같은 이름은 같은 ID
bash infra/scripts/deploy.sh image-ref a1b2c3d4          # plan에 쓸 이미지 주소(ECR 주소:태그)를 미리 정한다
bash infra/scripts/deploy.sh up       --id a1b2c3d4 --image <ecr_url>:a1b2c3d4-r1 --app app.json --plan-only   # 포트는 자동 할당. --id 대신 --name "프로젝트 이름" 도 된다
bash infra/scripts/deploy.sh build    --id a1b2c3d4 --source app.zip --dockerfile Dockerfile --tag a1b2c3d4-r1 [--arch ARM64]   # 승인 뒤: 이미지 빌드·푸시
bash infra/scripts/deploy.sh apply    a1b2c3d4     # 위에서 만든 승인된 저장 계획만 적용한다
bash infra/scripts/deploy.sh update   a1b2c3d4 --image <ecr_url>:a1b2c3d4-r2 [--app app.json] --plan-only
bash infra/scripts/deploy.sh rollback a1b2c3d4 --plan-only   # 직전 정상 이미지로 되돌린다
bash infra/scripts/deploy.sh status   a1b2c3d4
bash infra/scripts/deploy.sh diagnose a1b2c3d4     # 실패 분석 정보(JSON). 비밀은 마스킹된다
bash infra/scripts/deploy.sh db-check a1b2c3d4     # 앱 전용 DB 계정이 자기 DB만 쓸 수 있는지 확인
bash infra/scripts/deploy.sh destroy  a1b2c3d4     # 앱 삭제. 앱 전용 DB는 보존한다
bash infra/scripts/deploy.sh drop-db  a1b2c3d4     # 앱 전용 DB와 계정을 지운다(되돌릴 수 없다)
```

`up` 옵션: `--id` 또는 `--name`(둘 중 하나), `--port 8001|auto`(기본 auto), `--shared-db`(앱 전용 DB를 쓰지 않음), `--grace 초`(헬스체크 유예), `--skip-nat-check`, `--arch X86_64|ARM64`(기본은 이 PC의 docker 아키텍처. `deploy.sh detect-arch`. `build --arch`도 같은 값을 받아 계획과 같은 아키텍처로 빌드한다), `--yes`, `--plan-only`.

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
- **롤백**은 이력에서 **지금 배포된 것과 (이미지, 앱 설정)이 다른 가장 최근의 정상 항목**을 고르고(지금 배포된 것은 마지막으로 `apply`한 입력 `applied.*` 기준이다. 보관본이 없는 옛 배포만 Terraform 상태의 이미지로 이미지끼리 비교한다), 그 이미지가 정상이던 때의 **앱 설정도 함께** 복원한다. 그래서 업데이트가 실패했을 때도 마지막 정상 이미지로 돌아가고(이력의 마지막 줄로 "현재"를 정하면 실패한 업데이트 뒤에 이 이미지를 빼 버린다), `update --app`으로 포트·헬스체크 경로·크기·환경 변수를 바꿨어도 옛 이미지에 새 설정이 붙어 또 실패하지 않는다. foundation 값은 스냅샷이 아니라 지금 것을 쓴다. 스냅샷이 없는 옛 이력은 이미지만 되돌리고 경고한다. **사람이 명령으로 실행하며, 자동 복구는 만들지 않았다.**
- 롤백은 DB 스키마와 데이터를 되돌리지 않는다.
- **입력 되돌리기**: `update`와 `rollback`은 시작할 때 입력 파일(`*.auto.tfvars.json`)을 마지막으로 `apply`한 값으로 되돌린 뒤 변경을 적용한다. `--plan-only`로 계획만 만들고 버린 변경(이미지, 앱 설정, 롤백 스냅샷, 앱 전용 DB 전환)이 다음 계획에 섞이지 않게 하려는 것이다. 새 `update`·`rollback`을 하면 앞서 저장한 계획(`tfplan`)은 버려진다. 한 번도 `apply`하지 않은 배포(`up --plan-only` 직후)는 그대로 둔다.
- **계획과 입력의 일치**: 계획(`plan`)을 만들 때 입력 파일을 `plan.app.json`·`plan.platform.json`으로 고정한다. `apply`는 현재 입력이 이 고정본과 같을 때만 저장 계획을 적용하고, 다르면 적용하지 않고 계획을 다시 만들라고 안내한다. 정상 이력(`history/`)과 `applied.*`도 현재 입력 파일이 아니라 이 고정본으로 남기므로, 승인받은 계획과 다른 설정이 롤백 기준으로 저장되지 않는다. 새 계획을 만들기 시작하면 이전 계획과 고정본을 먼저 지우므로, 새 `plan`이 실패해도 입력과 어긋난 옛 계획이 남지 않는다. 이전 버전이 만든 계획(고정본 없음)은 적용하지 않고 다시 만들게 한다.
- **foundation 값 갱신**: `update`와 `rollback`은 plan 전에 foundation 출력을 다시 읽어 입력의 `foundation` 값을 갱신한다. `up` 때 복사한 값을 계속 쓰면 foundation을 다시 apply한 뒤(HTTPS 추가, ALB·보안 그룹 재생성) 사라진 리소스를 가리킨다.
- **이미지 확인**: 이미지가 ECR에 실제로 있는지 확인한다. 태그를 잘못 썼거나 롤백 대상이 보관 개수 제한으로 지워졌으면 태스크가 이미지를 받지 못해 서킷 브레이커까지 8분 넘게 기다리게 되므로 그 전에 중단한다(롤백이면 `--to`로 다른 이미지를 지정). `update`·`rollback`은 `--plan-only` 여도 plan 전에 확인한다. **`up --plan-only`만 건너뛴다**(승인 뒤에 빌드하는 흐름이라 계획 단계에는 이미지가 아직 없다). 모든 `apply`는 **적용 직전에 반드시 다시 확인**한다.
- **ID 검증과 폴더**: 모든 명령이 배포 ID 형식(소문자·숫자 4~8자)을 먼저 검사한다. `up`이 `apply` 시작 전에 실패하거나 취소되면 만들다 만 폴더를 지운다(`apply`가 시작된 뒤에는 state가 생길 수 있어 지우지 않는다). `destroy`가 끝난 폴더에는 `destroyed` 표식이 붙고, 같은 ID로 다시 `up`하면 `deployments/_destroyed/`로 옮겨진다. 앱 전용 DB를 `drop-db` 하지 않았다면 같은 ID로 다시 만들 때 그 DB를 다시 쓴다(접속 비밀번호는 새로 만든다).
- `diagnose`는 ECS 배포 상태와 실패 사유, 대상 헬스 사유, 서비스 이벤트, 중단된 태스크 사유, 최근 로그를 JSON으로 낸다. 접속 URL의 계정·비밀번호, AWS 키, `password`·`token`·`secret` 값은 가린다. 정규식 기반이라 모든 형태의 비밀을 보장하지는 않는다.
- **NAT 점검**: NAT를 쓰는 foundation이면 `up` 전에 NAT 인스턴스의 부팅 로그로 초기화 성공을 확인한다. 로그가 없으면 90초 기다린 뒤 실패로 본다(1시간 넘게 실행된 인스턴스는 로그가 밀려났을 수 있어 경고만 한다).

### 소스에서 이미지 만들기와 앱 초기화

- **`build`**: ZIP이나 폴더에서 이미지를 빌드해 foundation ECR에 올리고 이미지 주소를 표준 출력으로 낸다(진행 로그는 표준 오류). `--dockerfile`은 소스 안의 상대 경로이고 `..`·절대 경로는 거부한다. `--tag`를 주면 그 태그를 쓴다. `image-ref`로 정한 주소의 태그와 같게 쓰면 **계획(승인 전)과 빌드(승인 후)를 나눌 수 있다**. 빌드는 이 PC의 docker 아키텍처로 한다. ECR 로그인은 임시 docker 설정 폴더로 하고 종료·신호 때도 지운다. 호스트의 AWS 키와 docker 소켓은 빌드에 넘기지 않는다.
- **ZIP 풀기**: 경로 이탈(`../`, 절대 경로), 심볼릭 링크, 암호 ZIP, 파일 20000개 초과, 풀린 총 크기 1 GiB 초과(`ZIP_MAX_BYTES`), 압축률 200배 초과 파일(압축 폭탄)을 거부한다. 풀리는 실제 바이트를 세므로 헤더가 거짓이어도 한도를 지킨다. 압축 안에 최상위 폴더가 하나뿐이면(GitHub 아카이브) 그 폴더를 기준으로 삼는다.
- **앱 초기화 작업(`init_command`)**: 앱 설정(`app.json`)에 `"init_command": ["python", "-m", "backend.app.initialize_database"]`처럼 주면 `apply` 뒤에 앱 이미지로 **한 번** 실행한다(테이블 생성·마이그레이션). 앱과 같은 작업 정의에 명령만 바꿔 실행하므로 같은 이미지·DATABASE_URL·네트워크를 쓴다. 이 단계가 없으면 `/health`가 DB 연결만 확인하는 앱은 테이블이 없어도 헬스체크를 통과해서, 배포는 성공인데 가입·로그인이 500 오류인 상태가 된다(`sample-back`에서 확인).
  - 명령은 **여러 번 실행돼도 안전해야 한다**(`CREATE TABLE IF NOT EXISTS`). 같은 이미지·같은 명령으로 직전에 성공했으면(`init.done` 지문) 다시 돌리지 않는다. 설정만 바꾸는 `update`나 같은 이미지 재배포에서 약 55초를 아끼고 비멱등 명령의 중복 실행을 막는다. 새 앱 전용 DB를 만들면 기록을 지운다.
  - 실패하면 종료 코드·사유·로그를 보여 주고 헬스체크와 정상 이력 없이 실패로 기록한다. 제한 시간(`INIT_TIMEOUT`, 기본 300초)을 넘기면 작업을 멈추고(`stop-task`) 시간 초과로 실패 처리한다.
  - `init_command`는 앱 코드와 같은 신뢰 수준(앱의 DB 계정, 앱의 작업 역할)으로 돈다.

### 앱별 DB

기본으로 앱이 DB를 쓰면(`use_database: true`) 배포마다 **전용 DB `app_<id>`와 그 DB에만 권한이 있는 계정**을 만든다. 모든 앱이 DB 관리자 계정으로 같은 DB를 쓰면 앱 하나가 뚫렸을 때 다른 앱의 데이터까지 읽고 지울 수 있기 때문이다.

- 계획 단계(`--plan-only`)에서는 접속 정보 ARN만 쓰고 아무것도 만들지 않는다. **승인 후 `apply`에서** SSM 파라미터(`/<프로젝트>/apps/<id>/database-url`)와 DB·계정을 만든다.
- DB는 프라이빗 서브넷에 있어서 VPC 안에서 만든다. foundation에 **DB 준비 Lambda**(`enable_db_lambda`, 기본 켬)가 있으면 그것을 호출한다(`foundation/lambda/db_provisioner/`, 순수 파이썬 PyMySQL 포함). 실측으로 DB 격리 확인이 약 5초다. Lambda가 없으면(옛 foundation, `enable_db_lambda=false`) `mysql` 클라이언트를 한 번 실행하는 Fargate 작업으로 만든다(약 70초 더 걸리고, 클라이언트 이미지를 처음에 한 번 ECR에 올려야 해서 `docker`가 필요). `PAVED_DB_VIA_FARGATE=1`이면 Lambda가 있어도 Fargate 경로를 쓴다.
  - Lambda는 SSM에서 관리자 비밀번호와 앱 접속 정보를 읽는다. NAT 인스턴스를 켠 구성은 NAT를 거치고, 끈 구성은 SSM VPC 엔드포인트(월 약 $8~9)를 함께 만든다. 이 Lambda 역할도 DB 작업용 실행 역할처럼 관리자 비밀번호를 읽을 수 있는 대상이다.
  - 한계: Lambda의 DB 연결은 TLS로 암호화하지만 서버 인증서를 검증하지 않는다(순수 파이썬 패키징 때문에 `cryptography`를 넣지 않고 TLS를 택했다). RDS CA 번들을 함께 넣어 검증하는 것이 다음 개선이다.
- `destroy`는 앱만 지우고 **앱 전용 DB와 접속 정보는 보존한다**(데이터 보호). 지우려면 `drop-db` 또는 `destroy --drop-db`.
- `db-check`는 앱 전용 계정으로 접속해 자기 DB는 되고, 공유 DB(`app`)와 시스템 테이블은 막혀 있고, `SHOW DATABASES`에 다른 앱의 DB가 보이지 않는지 확인한다.
- **권한 분리**: 앱 태스크가 쓰는 공유 실행 역할은 DB 관리자 비밀번호를 읽을 수 없다. 관리자 비밀번호는 앱별 DB를 만드는 1회성 작업의 전용 실행 역할(`<project>-db-provisioner-execution`)만 읽는다. 또 배포 모듈은 `database_url_parameter_arn`이 **이 배포의** `/apps/<id>/database-url`일 때만 통과시킨다(다른 앱이나 관리자 접속 정보를 가리키면 plan에서 막힌다). 한계: 앱들이 실행 역할 하나를 같이 쓰므로 IAM 수준에서 앱별로 격리된 것은 아니다. 격리는 모듈의 사전 조건과 DB 계정 권한이 맡는다(배포마다 역할을 만들면 IAM 전파 지연이 배포 시간을 늘리고 역할 수 할당량에 걸린다).
- `--shared-db`를 주면 공유 DB를 관리자 계정으로 쓴다(이전 방식). 공유 DB의 테이블 충돌 문제가 그대로 있다. 이 선택은 배포에 기록되어(`db-shared`) 나중에 `update`가 몰래 앱 전용 DB로 바꾸지 않는다.
- **DB 없이 만든 배포를 `update`로 `use_database: true`로 바꾸면** 앱 전용 DB로 전환한다(승인 후 `apply`에서 DB와 접속 정보를 만든다). 전환하지 않으면 모듈이 foundation의 공유 URL(DB 관리자 계정)로 대체해서, 앱 전용 DB가 기본이라는 약속과 달리 조용히 모든 DB에 대한 관리자 권한이 붙는다.
- 테이블 생성은 앱 설정의 `init_command`(위 "앱 초기화 작업")로 한다. `sample-back`은 앱 시작과 분리된 `initialize_database` 모듈이 있어 이 방식과 맞는다. 팀의 최종 결정은 아직이다(`docs/OPEN_QUESTIONS.md`).

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
- **보안 그룹 소유권**: 앱 배포 모듈이 foundation이 만든 ALB 보안 그룹과 DB 보안 그룹에 앱별 규칙(ALB 아웃바운드, DB 3306 인바운드)을 추가·삭제한다. AGENTS.md 6장의 "앱 배포에서 foundation 리소스를 변경하지 않는다"와 부딪히는 지점이라 보안 그룹 소유권 합의가 필요하다(인프라 계약의 미결 항목). 앱 하나당 규칙은 보안 그룹별 1개이고 기본 할당량(60)이 포트 49개를 넘으므로 지금은 한도에 걸리지 않는다.
- 대상 그룹의 등록 해제 지연은 30초(`deregistration_delay_seconds`)다. 교체되는 태스크가 처리 중인 요청을 끝낼 시간이고, 업로드처럼 오래 걸리는 요청이 있으면 늘린다(ALB 기본은 300초).
- **RDS 보호**: 자동 백업 7일(기본), 삭제할 때 최종 스냅샷(기본), 삭제 보호(기본). 다중 AZ는 `db_multi_az`로 켠다. 교육용·무료 계정은 백업 보관 기간이나 다중 AZ를 제한할 수 있어서 그때는 변수로 낮춘다.
- `enable_nat_instance = false`면 앱 태스크가 퍼블릭 서브넷에서 퍼블릭 IP로 실행된다(시험용, 기본은 true). 앱 전용 보안 그룹이 ALB가 보내는 포트만 허용한다.
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
- D. `deploy.sh` 함수: `deploy_id` 규칙, 앱 전용 DB 이름·ARN, 비밀 마스킹, DB 작업 정의(로그 그룹·ARN·이미지가 입력과 같은지, 평문 비밀번호가 없는지), state 설정, NAT 부팅 로그 판정, 계획·입력 고정과 적용 차단, 이름→배포 ID, 아키텍처 감지, 앱 초기화(건너뛰기·시간 초과·실패 처리), ZIP 풀기 한도, build 임시 폴더 정리, DB 작업의 Lambda/Fargate 선택, 이미지 확인 시점
- E. 정적 검사: `terraform fmt`, `validate`, `bash -n`

판정은 Terraform이 실제로 내는 메시지로만 한다. 시험 환경이 고장난 것(초기화 실패 등)은 "차단 성공"이 아니라 **"시험환경오류"로 따로 센다**. 이 구분이 없으면 환경 오류가 전부 성공으로 읽힌다. 시험이 실제 실행과 같은 조건(Git Bash의 `/c/...` 경로)에서 도는지도 중요해서, 아래 AWS 시험에서 이 차이로 놓친 버그가 둘 있었다(고쳐서 시험에 반영했다).

### worker 시험

`python infra/worker/test_worker.py`. 시험용 HTTP 서버(`back/API.md`를 흉내)와 가짜 `deploy.sh`로 돌며 실제 AWS·Docker는 쓰지 않는다. 자세한 내용은 [worker/README.md](worker/README.md).

### AWS 시험

기준: 2026-10-09, 리전 `sa-east-1`(상파울루), HashiCorp Terraform 1.16.5, AWS provider 6.x, 로컬 Docker Desktop. 실제 적용은 담당자의 실행 요청 범위에서 했고, 매 `apply` 전에 계획을 확인했다.

**2026-10-10 추가로 확인한 것**(같은 계정·리전, `sample-back`을 앱으로 사용, 백엔드는 가짜)

- `build`로 ZIP에서 이미지를 빌드·푸시(약 20초, 캐시 사용). `up --plan-only`로 이미지가 없는 상태에서 계획을 만들고(약 25~35초) 승인 후 `apply`.
- **앱 초기화 작업**으로 테이블이 만들어져 `/api/ideas`가 DB 집계를 정상 응답(초기화가 없으면 `/health`만 통과). 같은 앱을 NAT를 켠 foundation(프라이빗 서브넷)에서도 배포해 동작을 확인했다.
- **시간**(RDS가 이미 있는 foundation에서 앱 1개): Fargate로 DB를 준비하던 때 `apply` 약 3분 6초(DB 준비 1분 41초, Terraform 19초, 초기화 56초, 헬스체크 6초). **Lambda로 DB를 준비하면 빌드 포함 124초**(빌드 20초 + 적용 104초). foundation 신규 생성(RDS 포함)은 약 7분(RDS 6분 17초). 초기화 작업(Fargate 기동)이 남은 시간의 가장 큰 덩어리다.
- **실패**(헬스체크 경로 오류): 시작 후 2분 24초에 4xx로 실패를 확정하고 사유를 기록했다. 그 사이 ALB는 대상이 전부 비정상이면 요청을 모든 대상에 보내서(fail-open) 앱 접속은 유지됐고, ECS가 헬스체크 실패 태스크를 교체하는 것이 실제 영향이었다. **롤백**은 1분 21초에 정상 복구(초기화 작업 55초 포함. 이 초기화는 이후 지문 확인으로 건너뛴다).
- DB 격리 확인(`db-check`)이 Lambda 경로에서 통과(자기 DB만 허용, 다른 DB와 시스템 테이블 거부, 다른 앱 DB가 보이지 않음).

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
- **코드 리뷰 지적 수정 후 실제 AWS로 확인한 것(2026-10-09, 읽기 전용 또는 임시 리소스를 바로 지움)**
  - `put-parameter --value file://...`: Git Bash에서 `cygpath -m` 경로(`C:/...`)로 넘기면 값이 그대로 저장된다(읽어서 일치 확인 후 삭제). 비밀번호를 명령줄 인자로 넘기지 않으려고 쓴다.
  - ECR 이미지 확인: 실제 저장소에서 있는 태그·다이제스트·`tools-` 태그는 통과하고, 없는 태그는 "이미지가 없습니다", 없는 저장소는 "확인하지 못했습니다"로 구분된다.
  - S3 state 확인: 있는 키는 성공, 없는 키는 `(404) ... Not Found`로 응답해 `foundation-state`의 판정 문구와 맞는다.
  - 헬스체크 대기: 두 번 부르던 AWS 조회를 한 번으로 합친 버전을 실제 배포 2건에 실행해 대상이 2개일 때(`healthy healthy`)도 통과했다.
  - ECR 보관 정책(`tools-` 규칙 + 앱 이미지 200개)은 실제 저장소에 미리보기로 적용해 정책이 유효함을 확인했다(삭제 대상 0개, 정책 자체는 적용하지 않음).
  - 현재 AWS의 foundation state에 새 코드로 `plan`: 추가 4(DB 작업 전용 역할·정책·관리형 정책 연결, 정책 교체 1), 변경 1(앱 공유 역할 정책에서 관리자 비밀번호 제거)뿐이고 다른 변화는 없다. 적용은 하지 않았다.
- 이전 구조에서 확인한 것(2026-10-08): foundation 생성·삭제, 앱 배포 4건, 프라이빗 서브넷 태스크의 NAT·SSM·RDS 경로, 세션이 DB에 있어 교체 후에도 로그인 유지.

**시험 중 발견해 고친 문제**

- `t4g.nano` NAT 초기화 실패(메모리 부족, 상태 검사는 정상으로 나옴) → 기본 `t4g.small`, nano·micro는 변수 검증이 차단.
- 롤링 교체 중 옛 태스크 때문에 일찍 "정상"으로 판정 → ECS 배포 `COMPLETED`까지 대기.
- Git Bash가 `/`로 시작하는 환경 변수 값(로그 그룹 이름)을 Windows 경로로 바꿔 DB 작업 정의 등록이 실패 → 변환을 끄되 그 호출에만 적용. 이 수정이 같은 줄의 `$(...)`까지 영향을 줘서 한 번 더 실패 → 값을 먼저 읽도록 수정.
- `db-check`가 MySQL 시스템 스키마 `performance_schema`를 다른 앱 DB로 오인 → 시스템 스키마 허용.
- 서킷 브레이커 판정 시간을 잘못 예상(공식으로는 약 3.5분, 실측 8분대) → 문서를 실측으로 정정.
- 회귀 시험이 위 버그들을 놓친 이유(로그 그룹 값을 확인하지 않음, 경로 형태가 실제와 다름, 시험 복사에 `backend_override.tf`가 섞임)를 고쳐 시험에 반영. 보강한 시험이 수정을 뺀 상태에서는 실제로 실패하는 것까지 확인.

**검증 안 됨**

- 새 foundation 코드(DB 작업 전용 실행 역할, 앱 역할의 관리자 비밀번호 권한 제거, ECR 보관 정책)는 `plan`과 정책 미리보기까지만 확인했고 실제 foundation에 적용하지 않았다. 적용 후 `db-check`·`up`이 새 역할로 동작하는지는 확인하지 못했다(옛 foundation에서는 `db_provisioner_execution_role_arn`이 없다는 안내가 나오며 중단한다).
- `up`이 `apply` 전에 실패했을 때 폴더를 지우는 동작, `destroy` 뒤 같은 ID 재사용, `update`·`rollback`의 입력 되돌리기, foundation 갱신, 계획 입력 고정·불일치 차단은 함수 단위 시험(모의 AWS)으로만 확인했고 실제 배포로 처음부터 끝까지 돌려 보지는 않았다.

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
