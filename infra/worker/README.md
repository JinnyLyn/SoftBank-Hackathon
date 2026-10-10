# worker (planner + executor)

플랫폼 백엔드(`back/`, PR #10·#23)와 `infra/scripts/deploy.sh`를 잇는 프로그램이다. 표준 라이브러리만 쓴다(Python 3.12 이상).

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

1. 분석 결과의 사용 규모·예산으로 최저·평균·최대 세 안(`lean` / `balanced` / `roomy`)과 월 비용을 계산하고 그중 하나를 고른다(`cost.py`, `prices.json`. 아래 "최저·평균·최대 세 안과 예산").
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
  `task_size`·`min_tasks`·`max_tasks`는 분석이 아니라 **선택한 구성 단계와 예산이 정한다**(분석 결과의 값은 무시).
- `init_command`는 앱 이미지로 apply 뒤에 한 번 실행한다(테이블 생성 등). 여러 번 실행돼도 안전해야 한다.
- `dockerfile`은 소스 안의 상대 경로다(생략하면 `Dockerfile`).
- **사용 규모·예산은 프로젝트 API의 값을 우선 읽는다.** 사용자가 프로젝트를 등록할 때 입력해 백엔드가 저장한 `expected_users`, `traffic_pattern`, `monthly_budget_usd`(`back/API.md`, 예산은 `"30.0000"` 같은 문자열)다. 프로젝트에 값이 없을 때만 위 분석 결과의 `scale`(백엔드에 저장 필드가 없던 때의 호환용)에서 읽으며, 항목마다 따로 대체한다. 그래서 분석기가 프로젝트 입력을 `scale`에 복사해 주지 않아도 사용자가 입력한 예산이 적용된다. 둘 다 없으면 `balanced`로 시작하고 예산 검사는 하지 않는다(세 안은 프리셋 그대로). 프로젝트 값이 잘못됐으면(숫자가 아닌 예산, 음수 등) 오류에 `프로젝트 <필드>`라고 출처를 밝히고 계획을 만들지 않는다.
- 비밀로 보이는 환경 변수 **이름**이나 `DATABASE_URL`은 거부한다. 이름이 무해해도 **값**이 키·토큰·접속 URL처럼 보이면(`sk-...`, `ghp_...`, AWS 키, JWT, 개인 키, `mysql://사용자:비밀번호@`) 거부한다. 값은 계획 변수와 작업 정의에 평문으로 남기 때문이다.

### 최저·평균·최대 세 안과 예산

월 예산(프로젝트의 `monthly_budget_usd`, 없으면 분석 결과의 `scale.monthly_budget_usd`)은 코드에 박은 상한 대신 사용자가 정하는 한도다. 예산이 있으면 `cost.budget_configs`가 그 금액을 넘지 않는 세 안을 만든다. 예산 판정은 평소 비용이 아니라 **부하가 최대일 때(오토스케일링이 `max_tasks`까지 늘었을 때)의 월 비용(`peak_monthly`)** 으로 한다. 그래야 부하가 몰려도 예산을 넘지 않는다.

| 안 | 구성 | 금액 기준 |
|---|---|---|
| 최저 `lean` | xsmall 1개 고정 | 예산과 무관하게 가장 작은 구성 |
| 평균 `balanced` | small, 최소 1개 | 최저와 **실제로 만든 최대 안**의 중간 금액까지 `max_tasks`를 채움 |
| 최대 `roomy` | medium, 최소 2개 | **예산 한도**까지 `max_tasks`를 채움 |

- `max_tasks`는 코드에 상한이 없고 예산이 허용하는 만큼 정해진다(예: foundation 기본 구성(3 AZ, NAT 3대)에서 월 $300이면 최대 안은 medium 2~3개, $1000이면 2~14개). 예산을 늘리면 최대 안도 따라 커진다. 단, 아래 "Fargate vCPU 할당량"이 이를 다시 제한한다.
- 평균 안의 기준은 예산 원금이 아니라 최대 안의 실제 금액이다. 태스크는 정수 개라 최대 안이 예산을 다 쓰지 못할 수 있어서, 원금 기준이면 평균이 중간보다 위로 치우친다(월 $250이면 최저 $91.40·최대 $199.73의 중간은 $145.57인데 원금 기준은 $168.78이 나왔다).
- 예산이 작아 최대 안의 최소 구성(medium 2개)을 못 담으면 평균 안을 예산 한도까지 채워 최대 안으로 삼는다. 그것도 못 담으면 최저 안만 남는다. 최저 안도 예산을 넘으면 계획을 만들지 않고 이유와 함께 종료한다.
- 예산이 없으면 예산 판정을 하지 않고 프리셋 세 안(1~1, 1~2, 2~4)을 그대로 쓴다.
- 이 중 추천 안은 예상 사용자 수와 접속 패턴으로 고른다. 예산·할당량 때문에 만들 수 없는 안이면 만들 수 있는 가장 큰 안으로 내린다. 이유 문구는 실제로 만든 안의 수만 말한다(안이 2개면 "2개 안은 모두…").
- 비교 금액은 두 가지다. `total_monthly`는 평소(`min_tasks`개) 비용, `peak_monthly`는 부하가 최대일 때 비용이다. 승인 화면으로 가는 `cost_estimate.amount`는 평소 비용이다.
- 제외 항목(NAT 인스턴스 EC2, ALB 처리 용량·데이터 전송, 로그·ECR·백업 저장)은 두 금액 모두에 들어 있지 않다. 그래서 실제 청구는 예산보다 클 수 있다. 이를 알아채려면 foundation의 `budget_monthly_usd` 알림(`infra/README.md`)을 쓴다.

### Fargate vCPU 할당량

예산만으로 `max_tasks`를 정하면 계정의 Fargate 할당량보다 큰 최대 구성이 나올 수 있다. AWS 문서의 기본 할당량("Fargate On-Demand vCPU resource count")은 **리전당 6 vCPU**라서, 예를 들어 월 $1000의 최대 안(medium 14개 = 14 vCPU)은 승인해도 최대 용량까지 늘지 못한다. 그래서 planner는 `foundation-info` 다음에 `fetch_fargate_capacity`로 한도를 읽고, 모든 안의 부하 최대 vCPU가 **남은 vCPU = 할당량 - 사용 중 - 예약** 안에 들게 `max_tasks`를 줄인다(`cost.recommend(..., max_vcpu=)`).

- **예약**: 승인 대기(`awaiting_approval`)·승인된(`approved`) 다른 프로젝트의 계획은 아직 배포되지 않아 CloudWatch 사용량에 없지만, 승인되면 최대 vCPU를 쓴다. 그래서 새 계획을 만들 때마다 백엔드의 모든 프로젝트 계획에서 이 상태의 계획이 쓸 최대 vCPU(`variables.cost.peak_vcpu`, 이 값이 없는 옛 계획은 `variables.app`의 `task_size` × `max_tasks`)를 합쳐 남은 vCPU에서 뺀다(`reserved_vcpu`). 같은 점검에서 먼저 만든 계획도 백엔드에 이미 등록돼 있어 다음 계획에 반영되므로, 할당량 6 vCPU를 두 신규 프로젝트가 각각 6씩 받아 합계 12 vCPU의 계획이 되지 않는다.
- 배포된(`consumed`) 계획은 예약하지 않는다. 이미 사용량에 잡히고, 삭제·실패한 배포의 예약이 영원히 남아 새 계획을 잘못 거절하는 것을 막기 위해서다.
- **기다림**: 다른 계획의 예약 때문에 계획을 못 만들지만 예약이 없었다면 만들 수 있었으면, 실패로 세지 않고 `retry_after`(120초)마다 다시 시도한다(`CapacityWait`, 로그에 "계획 대기"). 그 계획이 승인·배포·대체되면 예약이 풀려 계획된다. 예약과 무관하게 할당량이 모자라면 이전처럼 실패로 센다(3회 후 중단).

- 할당량: `aws service-quotas get-service-quota --service-code fargate --quota-code L-3032A538`. 적용된 값이 없는 계정은 `get-aws-default-service-quota`로 기본값을 읽는다.
- 할당량은 계정마다 다르다. 기본은 6이지만 증설된 계정이 있다(개발 계정에서는 512 vCPU로 조회됐다). 코드는 값을 박지 않고 항상 읽는다.
- 사용 중인 vCPU: CloudWatch `AWS/Usage`의 `ResourceCount`(차원 `Service=Fargate`, `Type=Resource`, `Resource=vCPU`, `Class=Standard/OnDemand`, 단위 vCPU)의 최근 30분 최댓값. 할당량 응답에 `UsageMetric`이 있으면 그 네임스페이스·차원을 우선한다. 데이터가 없으면 0으로 본다. 이 계정·리전의 모든 Fargate 작업(다른 앱 포함)이 포함된다.
- 줄어든 안은 `options[].quota_limited=true`이고 이유 문구에 "Fargate 할당량(남은 N vCPU)"이 들어간다. 최소 구성도 못 담는 안은 빠지고, 가장 작은 안(0.25 vCPU)도 못 담으면 계획을 만들지 않고 이유와 함께 종료한다.
- **읽지 못하면 멈춘다.** 한도를 모른 채 추천하지 않고 `PlanError`로 종료한다(`fetch_foundation`과 같은 원칙). 배포 계정에 `servicequotas:GetServiceQuota`, `servicequotas:GetAWSDefaultServiceQuota`, `cloudwatch:GetMetricStatistics` 권한이 필요하다. 권한이 없는 계정에서는 `--skip-quota-check`로 worker를 실행하면 조회를 건너뛰며(계획 요약에 "할당량 확인을 건너뜀"이 남는다) 이때는 할당량보다 큰 구성이 추천될 수 있다.
- 한계: **이미 배포된 앱이 오토스케일링으로 최대까지 늘어날 때 쓸 몫은 잡지 않는다.** 배포된 앱은 현재 사용 중인 vCPU만 반영되므로, 평소 적게 쓰는 여러 앱이 동시에 최대로 늘면 할당량을 넘을 수 있다(예약은 승인 대기·승인된 계획에만 한다). approved 계획이 이미 태스크를 띄운 뒤에는 그 몫이 사용량과 예약에 함께 잡혀 보수적이다. 롤링 배포 중 옛·새 태스크가 함께 떠 있는 순간의 vCPU도 반영하지 않고, 지표는 몇 분 늦게 올라온다. 두 worker가 동시에 계획하는 경우의 경합도 막지 않는다(worker는 한 대를 전제로 한다). 승인·apply 직전의 용량 재검증은 하지 않는다.

### 계획 변수 (프런트가 읽는 키)

`variables`에 `deploy_id`, `image`, `dockerfile`, `app`(앱 설정), **`source_sha256`(승인된 소스 ZIP의 지문)**, **`cpu_architecture`**, 그리고 프런트 표시용 `tier`, `recommended`, `headline`, `tradeoff`, `reason`, `resources`(`service`, `spec`, `monthlyUsd`, `why`), `cost`(`app_monthly`, `shared_monthly`, `peak_monthly`, `peak_vcpu`, `budget_usd`, `excluded`), **`fargate_vcpu`**(`quota_vcpu`, `used_vcpu`, `reserved_vcpu`(승인 대기·승인된 다른 계획의 예약), `available_vcpu`. 할당량 조회를 건너뛰었으면 `null`), **`options`**(최저·평균·최대 세 안의 비교: `tier`, `rank`(`lowest`/`average`/`highest`), `label`, `task_size`, `min_tasks`, `max_tasks`, `total_monthly`, `peak_monthly`, `peak_vcpu`, `quota_limited`, `headline`, `tradeoff`, `recommended`)를 담는다.
현재는 **권장 단계 하나만** 계획으로 등록하고, 나머지 안은 `options`로 비교 정보만 알린다. 단계마다 Terraform 저장 계획이 따로 필요한데 한 배포 폴더에는 계획 하나만 둘 수 있어서, 단계 비교용 여러 계획은 아직 만들지 않았다(프런트 `README`의 "추천 비교표"는 계획이 하나면 그 하나만 보여 준다). 사용자가 다른 안을 고르는 흐름(선택한 안을 worker에 전달해 그 안으로 다시 계획)은 백엔드·프런트와 계약을 맞춰야 한다(`docs/OPEN_QUESTIONS.md`).

## executor: 승인된 작업 실행

`POST /api/worker/deployments/claim`으로 작업을 가져오면(백엔드가 `provisioning`으로 바꿔 둔다) 다음을 한다.

1. 저장된 plan 파일의 SHA-256이 승인된 값(`terraform_plan_sha256`)과 같은지 확인한다. 다르면 **아무것도 하지 않고** `failed`로 보고한다.
   이어서 **소스 ZIP의 SHA-256이 승인된 계획의 `source_sha256`과 같은지** 확인한다(plan 해시는 인프라 계획만 덮으므로, 승인 뒤에 소스가 바뀌면 검토하지 않은 코드가 배포된다). 다르거나 파일이 없으면 `source_mismatch`로 `failed` 보고하고 빌드하지 않는다.
2. `deploy.sh build`로 ZIP에서 이미지를 빌드해 ECR에 올린다(계획에 적힌 이미지 주소와 같아야 한다). **계획에 기록한 `cpu_architecture`를 `--arch`로 넘겨** 계획과 같은 아키텍처로 빌드한다.
3. `deploy.sh apply`로 저장된 계획 그대로 적용한다. 헬스체크 대기에 들어가면 `deploying`을 보고한다.
4. 성공하면 접속 주소와 함께 `healthy`, 실패하면 `deploy.sh diagnose` 결과와 로그 끝부분을 담아 `failed`로 보고한다.

- **보고는 일시 오류(연결 실패, 5xx)면 최대 4번 다시 보낸다.** 4xx(상태 전이 거부 등)는 다시 보내도 같아서 바로 올린다. `deploying` 보고가 끝내 실패해도 apply 출력 읽기는 멈추지 않고, 끝난 뒤 `deploying`을 다시 보내고 `healthy`를 보낸다(백엔드는 `provisioning`에서 `healthy`로 바로 갈 수 없다).
- **제한 시간**(계획 15분, 빌드 20분, 적용 25분)을 넘기면 bash만이 아니라 **하위 프로세스(terraform·docker) 전체를 종료**하고 `failed`로 보고한다(Windows는 `taskkill /T`, 그 밖에는 프로세스 그룹 종료). 종료된 Terraform은 state 잠금이 남을 수 있어 `deploy.sh` 재시도 전에 확인이 필요하다.

**실패해도 자동 롤백하지 않는다.** 이는 미결 사항이 아니라 [AGENTS §5](../../AGENTS.md#5-처리-흐름과-계약)의 확정 정책이다. 첫 배포 실패에는 롤백이 없으며 AI 진단·수정안 → 새 계획 → 사용자 승인 → 재배포로 진행한다.

이후 실패한 일반 배포의 롤백은 [백엔드 승인 계약](../../back/API.md#실패-후-사용자-승인-롤백)을 따른다. 사용자가 이전 `healthy` 버전으로 되돌릴 것을 선택하면 새 rollback plan 초안을 만들고, 새 Terraform 저장 plan·SHA-256·diff 요약을 등록한다. 사용자가 새 fingerprint·digest·diff를 검토해 승인한 뒤에만 별도 롤백 작업을 대기열에 등록하고 승인된 저장 plan을 실행한다. 롤백 실패를 자동 재시도하거나 DB 스키마·데이터 복구와 동일시하지 않는다.

**현재 worker에는 rollback plan 생성·업로드·diff 등록과 롤백 전용 실행 연동이 구현돼 있지 않다.** 위 내용은 연결해야 할 계약이며 현재 end-to-end 지원을 뜻하지 않는다. 연동 전에는 제품 롤백 실행을 제공하지 않고 미지원 상태를 알린다. 이 공백을 사람이 `deploy.sh rollback`을 직접 실행하는 방식으로 대체하지 않는다. 저수준 CLI의 확인 프롬프트도 백엔드의 사용자 승인을 대신하지 않는다.

## connector: 사용자 AWS 연결 확인

백엔드(PR #23)에는 사용자가 CloudFormation 스택으로 만든 AWS 역할을 확인하는 worker 경로가 있다([back/API.md](../../back/API.md)). 사용자 AWS 연결은 기본 제품 흐름이 아니므로([PRODUCT_DIRECTION.md](../../docs/PRODUCT_DIRECTION.md)) 기존 연결 화면·API와의 호환을 위한 부분이다. 템플릿과 운영자 준비는 [infra/README.md](../README.md#사용자-aws-연결-기존-연결-화면백엔드-호환용)를 따른다.

매 점검에서 **배포 작업을 가져와 실행한 뒤**(계획 만들기 전)에 `GET /api/worker/connections/pending`을 읽고 연결마다 다음을 한다. 호환용 일이 승인된 배포를 기다리게 하지 않도록 배포가 먼저다.

1. `account_id`·`role_arn`이 아직 `null`이면(CloudFormation 콜백 전) **아무것도 하지 않고 기다린다.**
2. 역할 ARN·계정 ID·`external_id`의 형식을 백엔드와 같은 규칙으로 확인한다. 어긋나면 AWS를 부르지 않고 건너뛴다.
3. `aws sts assume-role --role-arn … --external-id …`로 실제로 맡아 본다. 임시 자격 증명은 출력하지 않고(`--query AssumedRoleUser.Arn`) 맡은 역할의 계정이 보고된 계정과 같은지만 본다.
4. 성공하면 `POST /api/worker/connections/{id}/complete`에 `account_id`·`role_arn`만 보낸다.
5. **사용자 쪽 거부**(`ValidationError`·`RegionDisabledException`, 다른 계정의 역할, 그리고 아래 조건을 만족한 `AccessDenied`)가 `conn_max_tries`(8회, 시도 간격 15초 ≈ 2분) 이어지면 `POST …/fail`로 보고한다. 방금 만든 역할은 IAM 전파 전에 잠깐 거부될 수 있어 바로 실패로 만들지 않는다.
   **`AccessDenied`는 worker 권한이 증명된 뒤에만 사용자 쪽 거부로 센다.** AWS는 신뢰 정책·ExternalId 불일치, 없는 역할, worker 주체의 `sts:AssumeRole` 권한 누락에 같은 `AccessDenied`를 돌려줘 구분할 수 없다. 그래서 이 프로세스에서 `AssumeRole`이 한 번이라도 성공하기 전에는 `fail`을 보내지 않고 횟수도 올리지 않으며, 연결마다 한 번 운영자가 볼 경고 로그만 남기고 계속 다시 시도한다. 올바른 스택까지 영구 오류로 만드는 것을 막기 위해서다. 대신 worker 권한이 없는 채로 잘못된 스택 하나만 있으면 그 연결은 `pending`으로 남는다(운영자가 로그로 확인한다).   사용자에게 보이는 오류에는 AWS 원문(운영자 계정의 IAM 주체 이름 포함)과 ExternalId를 넣지 않고 오류 코드와 역할 ARN, 확인할 곳만 적는다.
6. **worker 쪽 문제**(만료된 자격 증명, 네트워크, CLI 없음, 15초 시간 초과)는 연결을 실패로 만들지 않고 횟수도 올리지 않는다. 복구되면 이어서 확인한다. 거부·오류 모두 같은 연결은 15초 간격을 두고 다시 확인한다.
7. **시간 예산**: 한 점검에서 연결 확인에 쓰는 시간은 15초(`conn_budget`)로 묶는다. AWS CLI·네트워크가 멈춰도 연결 수만큼 배포가 밀리지 않고, 예산을 넘으면 남은 연결은 다음 점검으로 미룬다. 멈춘 연결은 재시도 간격 동안 건너뛰어 다른 연결이 차례를 얻는다.

- 보고가 연결 실패·5xx면 최대 4번 다시 보내고, 4xx(저장된 콜백 값과 불일치 등)는 다시 보내도 같아서 이 프로세스에서 그 연결을 더 다루지 않는다. 사람이 백엔드의 값을 확인한다.
- 대기 연결 목록을 읽지 못해도(연결 API가 없는 백엔드, 일시 오류) 배포 작업 가져오기와 계획 만들기는 계속한다. 처음 한 번만 로그로 알린다.
- worker의 AWS 자격 증명은 사용자 역할에 대한 `sts:AssumeRole` 권한이 있어야 한다. 없으면 `AssumeRole`이 한 번도 성공하지 못해 모든 연결이 `pending`으로 남고 로그에 경고가 찍힌다(`fail`로 확정하지 않는다). 운영자는 연결을 켜기 전에 `aws sts get-caller-identity`와 권한을 먼저 확인한다.
- 연결 ID·계정 ID·역할 ARN은 로그에 남지만 `external_id`와 임시 자격 증명은 남기지 않는다. 단, `aws` 명령 인자에는 `external_id`가 들어가므로 worker를 도는 PC의 프로세스 목록에서는 보인다.

## 지키는 규칙

- 승인된 저장 plan만 적용한다. 승인 뒤에 새 plan을 만들어 적용하지 않는다.
- 비밀(토큰, API 키, DB 비밀번호)은 로그와 이벤트에 남기지 않는다. 토큰은 환경 변수로만 받고 하위 프로세스(Terraform·Docker)에는 넘기지 않는다.
- worker 전용 경로(`/api/worker/*`)에만 `X-Worker-Token`을 붙인다.
- LLM을 호출하지 않는다.

## 한계 (정직하게)

- **실제 백엔드(PR #10)와 연결해서 시험하지 않았다.** `test_worker.py`는 `back/API.md`를 흉내 낸 서버와 가짜 `deploy.sh`로 돌고, `deploy.sh`와의 연결은 진짜 AWS로 따로 확인했다(상파울루, 가짜 백엔드).
- **연결 확인(connector)**: `test_worker.py`는 가짜 `aws` CLI로 돈다. 별도로 시험 계정에서 실제 AWS(스택 생성, `sts:AssumeRole` 성공·거부)와 로컬 Docker 백엔드(PR #23)로 한 번씩 확인했다. 확인하지 못한 것은 콜백 Lambda의 성공 경로, 공개 주소의 백엔드, 계정이 다른 교차 계정 구성이다([infra/README.md](../README.md#사용자-aws-연결-기존-연결-화면백엔드-호환용) 참고).
- 한 번에 작업 하나씩 순서대로 처리한다(동시 배포 없음). worker가 작업 도중 죽으면 그 배포는 `provisioning` 상태로 남는다.
- 비용은 `prices.json`의 가격표로 계산한다. 공용 ALB·RDS·**공인 IPv4(ALB 가용 영역별 + NAT용 탄력적 IP, 기준 구성 3개)** 를 포함하고, NAT 인스턴스 EC2 요금(가격 조회 실패)·ALB 처리 용량·데이터 전송·로그·백업은 제외했다(제외 항목은 계획 요약에 적힌다). 공인 IPv4 개수는 **배포된 foundation의 출력**(`infra/deployments/foundation.json`의 `task_subnet_ids` 개수 = 가용 영역 수, `assign_public_ip`, `nat_instance_count`)에서 센다. 읽을 수 없으면 `prices.json`의 `foundation_assumptions`(foundation 기본값: 가용 영역 3개 + NAT 3대)를 쓴다. 기준일 2026-10-10.
- 계획 만들기가 약 30초, 승인 뒤 배포가 약 2분(DB 준비 Lambda 사용, 빌드 포함 124초 실측)이다. 프런트의 5분 대기 제한 안에 들어온다.

## 시험

```bash
python infra/worker/test_worker.py      # 106개(약 2.5분). 실제 AWS·Docker 없이 돈다
python infra/scripts/test_infra.py      # deploy.sh와 Terraform 모듈 시험
```
