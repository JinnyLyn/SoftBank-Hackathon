# worker (planner + executor)

플랫폼 백엔드(`back/`, PR #10)와 `infra/scripts/deploy.sh`를 잇는 프로그램이다. 표준 라이브러리만 쓴다(Python 3.12 이상).

현재 제품 기준은 [PRODUCT_DIRECTION.md](../../docs/PRODUCT_DIRECTION.md)의 `2026-10-10-managed-domains-v1`이다. 아래 AWS 자격 증명과 foundation 준비는 운영자/worker 환경용이며 사용자 계정·IAM 키 입력을 전제하지 않는다. 계정 이전은 [OPERATOR_AWS.md](../../docs/OPERATOR_AWS.md)를 따른다.

이 worker의 `healthy`는 앱 배포 상태다. 도메인 신규 등록·DNS·인증서·호스트 라우팅의 제품 흐름은 아직 포함하지 않는다. 기존 확보 도메인 연결(A) / 신규 구매 자동화(B)의 시연 범위와 파트 간 계약을 먼저 확인하며, 프런트 MOCK의 `status.domain`을 현재 worker의 출력으로 가정하지 않는다. 회의록의 도메인 목록은 구현 후보이며 일괄 실행 지시가 아니다.

백엔드는 ZIP·분석 결과·계획·승인·배포 대기열을 저장만 하고 Terraform·Docker·AWS를 실행하지 않는다(`back/API.md`).
프런트는 계획이 생길 때까지 기다리기만 한다. 그 사이를 이 worker가 채운다.

```
프런트 ──ZIP──▶ 백엔드 ◀──분석 결과── (분석 담당 모듈)
                  │  ▲
   계획 없는 프로젝트│  │계획 등록 + plan 파일 업로드          ┌─ planner ─┐
                  ▼  │                                      │           │
                 worker ── deploy.sh up --plan-only ────────┘           │
                  │                                                     │
  사용자 승인 ─▶ 대기열 ─▶ claim ─▶ executor: build → apply → 상태 보고 (deploying → healthy | failed)
```

## 실행

```bash
export WORKER_API_TOKEN=<백엔드와 같은 값>      # 값은 저장소·로그에 남기지 않는다
export AWS_REGION=sa-east-1                      # AWS 자격 증명은 aws configure 또는 환경에 이미 설정돼 있어야 한다
python infra/worker/worker.py                    # 계속 돌며 5초마다 점검 (--poll 로 변경)
python infra/worker/worker.py --once             # 한 번만 점검
```

| 설정 | 기본값 |
|---|---|
| `PLATFORM_API_URL` / `--api-url` | `http://127.0.0.1:8000` |
| `DEPLOY_SH` / `--deploy-sh` | `infra/scripts/deploy.sh` |
| `TARGET_ARCH` / `--arch` | 이 PC의 docker 기준(`deploy.sh detect-arch`) |
| `BASH_EXE` | Windows에서 Git Bash를 못 찾을 때 경로 지정 |

필요한 것: `bash`(Windows는 Git Bash), `terraform`, `aws` CLI, `docker`. foundation이 먼저 apply돼 있어야 한다.
**worker는 백엔드와 같은 PC에서 돌린다.** 소스 ZIP 경로(`source_path`)와 Terraform 작업 폴더(`infra/deployments/`)가 그 PC에 있다.

## planner: 계획 만들기

백엔드에서 계획이 없는 프로젝트를 찾고(`GET /api/projects`, `/plans`), 분석 결과가 있으면 다음을 한다.

1. 분석 결과의 사용 규모·예산으로 구성 단계(`lean` / `balanced` / `roomy`)와 월 비용을 계산한다(`cost.py`, `prices.json`).
2. `deploy.sh make-id`로 배포 ID, `deploy.sh image-ref`로 이미지 주소를 정한다(이미지는 아직 만들지 않는다).
3. `deploy.sh up --plan-only`로 Terraform 계획을 만든다. **Docker 빌드는 하지 않는다**(승인 전).
4. plan 파일의 SHA-256을 계산해 `POST /api/plans`로 등록하고, 파일을 `POST /api/worker/plans/{id}/terraform-plan`으로 올린다.

실패하면 같은 프로젝트를 2분 뒤에 다시 시도하고 3번 실패하면 멈춘다. 백엔드 등록(`POST /api/plans`)이 실패하면 적용한 적 없는 배포 폴더는 지워서 같은 ID로 다시 시도할 수 있게 한다. **계획 등록은 됐는데 plan 파일 업로드가 실패하면 로컬 plan 파일을 지우지 않는다**(지우면 승인된 작업이 `plan_mismatch`로 영영 실패한다). 점검마다 로컬 파일의 SHA-256이 등록된 값과 같을 때만 다시 올린다. 계획이 모두 `superseded`(대체됨)로만 남은 프로젝트는 다시 계획하고, 승인 대기·승인·배포됨 계획이 하나라도 있으면 건너뛴다. 이전에 적용한 적 없는 폴더는 지우고 새로 만들며, 이미 적용된 폴더는 거부한다. 프로젝트 목록은 `next_cursor`를 따라 전체(최대 10페이지)를 읽는다.

### 분석 결과의 계약 (LLM 담당과 합의 필요)

백엔드는 분석 결과(`result`)의 필드를 고정하지 않았다. worker는 아래 필드를 읽는다.

```json
{
  "app_config": {
    "container_port": 8000,
    "health_check_path": "/health",
    "use_database": true,
    "environment": { "COOKIE_SECURE": "false" },
    "init_command": ["python", "-m", "backend.app.initialize_database"]
  },
  "dockerfile": "sample-back/Dockerfile",
  "scale": { "expected_users": "~1,000", "traffic_pattern": "steady", "monthly_budget_usd": 120 }
}
```

- `app_config`는 [`app-config.schema.json`](../modules/ecs-web-app/app-config.schema.json)과 같은 규칙으로 검증한다. 검증에 실패하면 계획을 만들지 않는다.
  `task_size`·`min_tasks`·`max_tasks`는 분석이 아니라 **선택한 구성 단계가 정한다**(분석 결과의 값은 무시).
- `init_command`는 앱 이미지로 apply 뒤에 한 번 실행한다(테이블 생성 등). 여러 번 실행돼도 안전해야 한다.
- `dockerfile`은 소스 안의 상대 경로다(생략하면 `Dockerfile`).
- `scale`은 백엔드 API에 사용 규모·예산을 받는 곳이 없어서 분석 결과에 담는 방식으로 정했다. 없으면 `balanced`로 시작하고 예산 검사는 하지 않는다.
- 비밀로 보이는 환경 변수 **이름**이나 `DATABASE_URL`은 거부한다. 이름이 무해해도 **값**이 키·토큰·접속 URL처럼 보이면(`sk-...`, `ghp_...`, AWS 키, JWT, 개인 키, `mysql://사용자:비밀번호@`) 거부한다. 값은 계획 변수와 작업 정의에 평문으로 남기 때문이다.

### 계획 변수 (프런트가 읽는 키)

`variables`에 `deploy_id`, `image`, `dockerfile`, `app`(앱 설정), **`source_sha256`(승인된 소스 ZIP의 지문)**, **`cpu_architecture`**, 그리고 프런트 표시용 `tier`, `recommended`, `headline`, `tradeoff`, `reason`, `resources`(`service`, `spec`, `monthlyUsd`, `why`), `cost`를 담는다.
현재는 **권장 단계 하나만** 계획으로 등록한다. 단계마다 Terraform 저장 계획이 따로 필요한데 한 배포 폴더에는 계획 하나만 둘 수 있어서, 단계 비교용 여러 계획은 아직 만들지 않았다(프런트 `README`의 "추천 비교표"는 계획이 하나면 그 하나만 보여 준다).

## executor: 승인된 작업 실행

`POST /api/worker/deployments/claim`으로 작업을 가져오면(백엔드가 `provisioning`으로 바꿔 둔다) 다음을 한다.

1. 저장된 plan 파일의 SHA-256이 승인된 값(`terraform_plan_sha256`)과 같은지 확인한다. 다르면 **아무것도 하지 않고** `failed`로 보고한다.
   이어서 **소스 ZIP의 SHA-256이 승인된 계획의 `source_sha256`과 같은지** 확인한다(plan 해시는 인프라 계획만 덮으므로, 승인 뒤에 소스가 바뀌면 검토하지 않은 코드가 배포된다). 다르거나 파일이 없으면 `source_mismatch`로 `failed` 보고하고 빌드하지 않는다.
2. `deploy.sh build`로 ZIP에서 이미지를 빌드해 ECR에 올린다(계획에 적힌 이미지 주소와 같아야 한다). **계획에 기록한 `cpu_architecture`를 `--arch`로 넘겨** 계획과 같은 아키텍처로 빌드한다.
3. `deploy.sh apply`로 저장된 계획 그대로 적용한다. 헬스체크 대기에 들어가면 `deploying`을 보고한다.
4. 성공하면 접속 주소와 함께 `healthy`, 실패하면 `deploy.sh diagnose` 결과와 로그 끝부분을 담아 `failed`로 보고한다.

- **보고는 일시 오류(연결 실패, 5xx)면 최대 4번 다시 보낸다.** 4xx(상태 전이 거부 등)는 다시 보내도 같아서 바로 올린다. `deploying` 보고가 끝내 실패해도 apply 출력 읽기는 멈추지 않고, 끝난 뒤 `deploying`을 다시 보내고 `healthy`를 보낸다(백엔드는 `provisioning`에서 `healthy`로 바로 갈 수 없다).
- **제한 시간**(계획 15분, 빌드 20분, 적용 25분)을 넘기면 bash만이 아니라 **하위 프로세스(terraform·docker) 전체를 종료**하고 `failed`로 보고한다(Windows는 `taskkill /T`, 그 밖에는 프로세스 그룹 종료). 종료된 Terraform은 state 잠금이 남을 수 있어 `deploy.sh` 재시도 전에 확인이 필요하다.

**실패해도 자동 롤백하지 않는다.** 자동 복구 범위가 팀 결정 전이다(AGENTS.md 6장). 되돌리려면 `deploy.sh rollback <id>`를 사람이 실행한다.

## 지키는 규칙

- 승인된 저장 plan만 적용한다. 승인 뒤에 새 plan을 만들어 적용하지 않는다.
- 비밀(토큰, API 키, DB 비밀번호)은 로그와 이벤트에 남기지 않는다. 토큰은 환경 변수로만 받고 하위 프로세스(Terraform·Docker)에는 넘기지 않는다.
- worker 전용 경로(`/api/worker/*`)에만 `X-Worker-Token`을 붙인다.
- LLM을 호출하지 않는다.

## 한계 (정직하게)

- **실제 백엔드(PR #10)와 연결해서 시험하지 않았다.** `test_worker.py`는 `back/API.md`를 흉내 낸 서버와 가짜 `deploy.sh`로 돌고, `deploy.sh`와의 연결은 진짜 AWS로 따로 확인했다(상파울루, 가짜 백엔드).
- 한 번에 작업 하나씩 순서대로 처리한다(동시 배포 없음). worker가 작업 도중 죽으면 그 배포는 `provisioning` 상태로 남는다.
- 비용은 `prices.json`의 가격표로 계산한다. 공용 ALB·RDS·**공인 IPv4(ALB 가용 영역별 + NAT용 탄력적 IP, 기준 구성 3개)** 를 포함하고, NAT 인스턴스 EC2 요금(가격 조회 실패)·ALB 처리 용량·데이터 전송·로그·백업은 제외했다(제외 항목은 계획 요약에 적힌다). 공인 IPv4 개수는 **배포된 foundation의 출력**(`infra/deployments/foundation.json`의 `task_subnet_ids` 개수 = 가용 영역 수, `assign_public_ip`, `nat_instance_count`)에서 센다. 읽을 수 없으면 `prices.json`의 `foundation_assumptions`(foundation 기본값: 가용 영역 3개 + NAT 3대)를 쓴다. 기준일 2026-10-10.
- 계획 만들기가 약 30초, 승인 뒤 배포가 약 2분(DB 준비 Lambda 사용, 빌드 포함 124초 실측)이다. 프런트의 5분 대기 제한 안에 들어온다.

## 시험

```bash
python infra/worker/test_worker.py      # 70개(약 1.5분). 실제 AWS·Docker 없이 돈다
python infra/scripts/test_infra.py      # deploy.sh와 Terraform 모듈 시험
```
