# 백엔드 API 연동 문서

프런트엔드와 백엔드 연동을 위한 현재 API 계약입니다. 구현의 상세 스키마는 실행 중인 FastAPI 문서(`/docs`)와 OpenAPI JSON(`/openapi.json`)을 기준으로 합니다.

제품 방향은 [PRODUCT_DIRECTION.md](../docs/PRODUCT_DIRECTION.md)의 `2026-10-10-managed-domains-v1`을 따릅니다. 운영자 관리형 AWS와 사용자 독립 도메인이 목표지만, **도메인 조회·신규 등록·연결·상태 API는 현재 이 문서의 구현 계약에 포함되지 않습니다.** 프런트 `JinVibe:f169d27`의 `/api/domains/check`, `/api/projects/{id}/domain`, `/api/projects/{id}/domain/approve`, `status.domain`은 제안입니다. 이번 시연의 A/B 범위와 파트 간 계약을 먼저 확인하며 구매 자동화를 필수 구현으로 가정하지 않습니다. 기존 연결 API의 존재가 사용자 AWS 연결을 필수로 요구하는 근거는 아닙니다.

## 접속 및 공통 규칙

- 로컬 기본 주소: `http://127.0.0.1:8000`
- 개발 서버 실행 방법과 DB 준비: [`README.md`](README.md)
- JSON 요청: `Content-Type: application/json`
- 시간: UTC ISO 8601 문자열
- ID: UUID 문자열
- 오류 응답은 `{"error": "설명"}` 형식입니다. 입력 검증 오류(422)는 `details` 배열에 필드 경로와 메시지도 포함합니다. 없는 리소스는 404, 상태 충돌은 409를 반환합니다.
- 브라우저에서 다른 origin으로 호출하려면 서버의 `CORS_ORIGINS`에 프런트 origin을 설정해야 합니다. 기본은 CORS 비활성화입니다.
- 사용자 인증은 아직 이 API 계약에 포함되지 않았습니다. `/api/worker/*`는 `X-Worker-Token` 인증이 필요하며 프런트에서 호출하면 안 됩니다.

## 프런트엔드 주요 흐름

### 1. 프로젝트 소스 등록

#### ZIP 업로드

`POST /api/projects?name={프로젝트명}&filename={파일명}&expected_users=~1,000&traffic_pattern=peak&monthly_budget_usd=30&purpose=서비스설명`

- Body: ZIP 원본 바이트
- Header: `Content-Type: application/zip` (또는 `application/octet-stream`)
- 파일은 압축 해제하지 않고 저장합니다. 기본 최대 크기는 200 MiB이며 서버 설정으로 조정할 수 있습니다.
- `expected_users`, `traffic_pattern`, `monthly_budget_usd`, `purpose`는 선택 입력입니다. 프런트 입력 계약에 맞춰 사용자 규모(`~100`, `~1,000`, `~10,000`, `10,000+`), 접속 패턴(`steady`, `peak`, `unknown`), USD 월 예산(0 이상), 서비스 설명(최대 2,000자)을 프로젝트 메타데이터로 저장합니다. 기존 요청은 그대로 유효하며 값이 없으면 `null`입니다.
- `purpose`는 비밀값으로 판단되는 키·값이 포함되면 저장을 거부합니다. 프로젝트 메타데이터는 분석 결과를 대신하지 않으며 LLM/추천 모듈이 별도로 읽어 사용해야 합니다.

#### GitHub 저장소 등록

`POST /api/projects/github`

```json
{
  "name": "sample-app",
  "repository_url": "https://github.com/owner/repository",
  "ref": "main",
  "expected_users": "~1,000",
  "traffic_pattern": "peak",
  "monthly_budget_usd": 30,
  "purpose": "동아리 출석 체크"
}
```

`ref`는 생략할 수 있습니다. 비공개 저장소 접근 토큰은 서버의 `GITHUB_TOKEN` 환경 변수로 전달합니다.

#### 응답 및 목록

등록 응답은 `201 Created`이며 다음 형태입니다. ZIP 원본 자체나 서버 저장 경로는 응답에 포함되지 않습니다.

```json
{
  "id": "UUID",
  "name": "sample-app",
  "source_filename": "repository.zip",
  "source_sha256": "64자리 소문자 hex",
  "source_size_bytes": 12345,
  "source_type": "zip",
  "source_url": null,
  "source_ref": null,
  "expected_users": "~1,000",
  "traffic_pattern": "peak",
  "monthly_budget_usd": "30.0000",
  "purpose": "동아리 출석 체크",
  "created_at": "2026-10-10T00:00:00Z"
}
```

GitHub 입력이면 `source_type`은 `github`이고 URL/ref가 채워집니다. 네 가지 규모/예산 입력도 응답에 포함됩니다.

- `GET /api/projects?limit=20&cursor={next_cursor}`: `{ "items": [...], "next_cursor": "..." }` 반환. `limit`은 1~100입니다.
- `GET /api/projects/{project_id}`: 프로젝트 상세 반환.

### 2. 분석 결과 기록 및 조회

LLM 분석 요청 자체는 이 API가 수행하지 않습니다. 분석 담당 모듈이 결과를 만든 다음 기록합니다.

`POST /api/projects/{project_id}/analyses`

```json
{
  "schema_version": "팀에서 합의한 버전",
  "source_sha256": "등록된 프로젝트의 SHA-256",
  "result": {}
}
```

`result`는 JSON 객체이며 최대 128 KiB입니다. 프로젝트 원본 해시와 다르면 409, 민감한 키로 판단되는 필드가 있으면 422입니다. 저장 응답에는 분석 `id`, `project_id`, schema version, source hash, 결과와 생성 시각이 포함됩니다.

- `GET /api/projects/{project_id}/analyses/latest`: 최신 분석 결과. 아직 없으면 404.
- 분석 JSON의 구체적인 필드 계약은 LLM 담당자와 프런트 담당자가 별도로 합의해야 합니다. 백엔드는 이를 고정하지 않았습니다.

### 3. 배포 계획 확인 및 승인

#### 계획 생성

`POST /api/plans`

```json
{
  "project_id": "UUID",
  "analysis_id": "UUID 또는 null",
  "target": "aws",
  "module_id": "모듈 식별자",
  "variables": {},
  "summary": "계획 설명",
  "cost_estimate": {
    "amount": "12.34",
    "currency": "USD",
    "period": "month",
    "pricing_as_of": "2026-10"
  },
  "terraform_plan_sha256": "64자리 소문자 hex"
}
```

AWS 계획에는 월 비용 추정치와 Terraform plan SHA-256이 필요합니다. 응답에는 서버가 만든 `fingerprint`, `status: "awaiting_approval"`, `terraform_plan_ready` 등이 포함됩니다. 이 fingerprint는 사용자가 승인하는 계획 내용의 식별자이므로 프런트가 임의로 계산하거나 바꾸면 안 됩니다.

현재 비용 추정 통화는 `USD`만 허용합니다. 프런트 이력의 `monthlyUsd` 필드는 이 금액을 그대로 표시합니다.

- `GET /api/projects/{project_id}/plans`: 프로젝트 계획 목록
- `GET /api/plans/{plan_id}`: 계획 상세와 fingerprint

#### 승인

`POST /api/plans/{plan_id}/approve`

```json
{ "expected_fingerprint": "계획 응답에 포함된 fingerprint" }
```

승인 대기 상태이고 fingerprint가 일치할 때만 승인됩니다. AWS 계획은 SHA-256이 일치하는 Terraform plan 파일이 먼저 저장되어 있어야 합니다. 성공 후 `status`는 `approved`가 됩니다.

### 4. 배포 대기열 등록 및 상태 조회

#### 배포 요청

`POST /api/deployments`

```json
{
  "plan_id": "UUID",
  "expected_fingerprint": "승인한 계획의 fingerprint"
}
```

성공하면 HTTP 202와 `status: "queued"`인 배포 이력이 반환됩니다. 승인 이후 계획이나 fingerprint가 바뀌었거나 plan artifact 검증이 실패하면 409입니다. 이 응답은 **실제 AWS 배포 완료가 아니라 worker 실행 대기 등록**을 뜻합니다.

#### 상태 및 이력

- `GET /api/projects/{project_id}/status`: 프런트용 최근 상태 `{ "state": "running|success|failed", "log": [...], "url": null }`. 배포 이력이 아직 없으면 404.
- `GET /api/deployments?project_id={UUID}&limit=50`: 전체 또는 프로젝트별 프런트 요약 이력 목록. `limit`은 1~100입니다. 각 항목에는 리소스 식별자와 함께 `app`, `version`, `tier`, `provider`, `target`, `monthlyUsd`, `status`(`running/success/failed`), `createdAt`이 포함됩니다. 여기서 `status`는 요약 상태입니다.
- `GET /api/deployments/{deployment_id}`: 배포 상세와 이벤트 목록. `operation_type`은 일반 배포 `deploy` 또는 사용자 승인 롤백 `rollback`이며, 롤백 이력에는 `rollback_from_deployment_id`, `rollback_to_deployment_id`가 포함됩니다.

상세 응답과 배포 생성 응답의 내부 상태는 `queued`, `provisioning`, `deploying`, `healthy`, `failed`입니다. 이전 구현과의 호환을 위해 `rolling_back`, `rolled_back` 값은 읽을 수 있지만 새 롤백 작업에는 사용하지 않습니다. 프로젝트 status endpoint와 목록의 프런트 요약 상태 `running/success/failed`는 내부 상태를 단순화한 값입니다. 성공 시 공개 URL은 `url`에 들어갑니다.

#### 실패 후 사용자 승인 롤백

첫 배포 실패는 롤백할 이전 `healthy` 버전이 없으므로 AI 실패 원인 분석·수정안과 새 계획 승인 흐름으로만 진행합니다. 자동 롤백은 없습니다.

`GET /api/deployments/{deployment_id}/rollback-candidate`는 실패한 일반 배포에서만 호출합니다.

```json
{
  "rollback_available": true,
  "reason": "available",
  "target_deployment_id": "직전 healthy 배포 UUID",
  "target_plan_id": "직전 healthy 배포의 plan UUID"
}
```

롤백 불가 시 `reason`은 `first_deployment`, `no_previous_healthy`, `not_failed` 중 하나입니다. `first_deployment`와 `no_previous_healthy`에서는 AI 분석·수정 → 새 plan/비용/terraform plan 확인 → 사용자 승인 → `POST /api/deployments`로 재배포합니다.

사용자가 롤백 계획 생성을 명시적으로 선택·확인한 경우에만 아래 요청을 보냅니다. 이 요청은 실행을 승인하지 않으며 새 `awaiting_approval` rollback plan을 반환합니다.

`POST /api/deployments/{deployment_id}/rollback`

```json
{
  "expected_target_deployment_id": "rollback-candidate에 표시된 UUID"
}
```

백엔드는 실패 배포가 해당 프로젝트의 최신 배포인지와, 실제 `healthy` 전환 시각이 가장 최근인 이전 정상 버전인지 확인합니다. 후속 배포가 있으면 오래 열린 화면의 롤백 요청은 409으로 거부합니다. worker는 새 rollback plan ID로 Terraform plan을 다시 생성해 `POST /api/worker/rollback-plans/{plan_id}/terraform-plan`에 SHA-256과 함께 저장하고, `POST /api/worker/rollback-plans/{plan_id}/summary`로 diff 요약을 저장합니다. 사용자는 변경된 fingerprint·digest·diff를 확인한 뒤 `POST /api/plans/{plan_id}/approve`로 승인하며, 이후에만 `POST /api/deployments`가 별도 `operation_type: "rollback"` 실행 이력을 생성합니다.

이 전제는 초안 생성 이후에도 승인·큐 등록·claim·진행 상태 보고에서 재검증합니다. 같은 프로젝트에 후속 배포 또는 다른 대기·진행 작업이 생기면 승인·등록·진행 보고는 409입니다. claim에서는 오래된 롤백을 `failed`로 바꾸고 `rollback_context_invalidated` 이벤트를 남긴 뒤 다음 작업을 찾습니다. 실패 보고는 계속 허용합니다. 롤백 실행이 `provisioning`·`deploying`인 동안 같은 프로젝트의 새 배포 등록도 409이며 종료 후 재요청할 수 있습니다. 다른 프로젝트의 배포는 영향을 주지 않습니다.

## AWS 연결 API

- `GET /api/connections`: AWS 연결 목록
- `POST /api/connections`: `{ "provider": "aws", "name": "연결 이름", "fields": {} }`로 연결 요청 생성
- `PUT /api/connections/{connection_id}`: 연결 이름/설정 수정
- `POST /api/connections/{connection_id}/check`: 저장된 연결 상태 조회. 현재 AWS 자격 증명을 직접 호출해 실시간 검증하는 endpoint는 아닙니다.
- `DELETE /api/connections/{connection_id}`: 연결 삭제

연결 생성 결과에는 `status: "pending"`과 설정용 `setupUrl`이 포함될 수 있습니다. 공개 HTTPS 템플릿 주소는 `AWS_CONNECTION_TEMPLATE_URL`, worker의 운영자 계정 ID는 `PLATFORM_AWS_ACCOUNT_ID`, CloudFormation 콘솔 리전은 `AWS_REGION` 환경 변수로 받습니다. 현재 로컬 `back/.env`에는 운영자 계정 ID와 리전 `sa-east-1`이 설정되어 있습니다. 해당 `.env`는 Git에서 제외됩니다. 링크에는 `param_PlatformAccountId`와 연결별 `param_ExternalId`를 추가합니다.

worker 경로는 다음과 같습니다.

- `GET /api/worker/connections/pending`: 인증된 worker가 대기 연결 목록(ID, `external_id`, 입력 필드)을 조회합니다.
- `POST /api/worker/connections/{connection_id}/fail`: `{ "error": "실패 원인" }`으로 실패를 보고합니다. 오류는 저장 전에 민감값을 마스킹하며 같은 실패 재시도는 멱등 처리합니다.
- `POST /api/worker/connections/{connection_id}/complete`: `{ "account_id": "<사용자 AWS 계정 ID>", "role_arn": "arn:aws:iam::<같은 사용자 계정 ID>:role/PavedCloudsReadOnlyRole" }` 형식으로 완료를 보고합니다. 계정 ID와 IAM role ARN의 계정 부분이 일치해야 하며 값은 `connections.aws_account_id`, `connections.role_arn`에 저장됩니다. 같은 완료 재시도는 멱등 처리합니다. `PLATFORM_AWS_ACCOUNT_ID`는 이와 별개로 사용자 역할의 trust policy가 신뢰할 worker 운영 계정입니다.

이 API는 ARN 구문과 계정 ID만 대조하며 AWS STS로 역할 존재나 실제 권한을 검증하지 않습니다. worker가 AssumeRole/GetCallerIdentity 등 AWS 검증에 성공한 뒤 보고해야 합니다. 템플릿이 공개 HTTPS 주소에 올라가 `AWS_CONNECTION_TEMPLATE_URL`이 설정되기 전에는 setup 링크가 만들어지지 않습니다.

## Worker 전용 API

아래 경로는 프런트 호출용이 아닙니다. 모든 요청에 `X-Worker-Token: {WORKER_API_TOKEN}`이 필요합니다.

- `POST /api/worker/plans/{plan_id}/terraform-plan`: `Content-Type: application/octet-stream`으로 Terraform binary plan 업로드. 서버가 SHA-256을 확인합니다.
- `POST /api/worker/rollback-plans/{plan_id}/terraform-plan`: 새 rollback Terraform binary plan을 저장합니다. `X-Terraform-Plan-SHA256` 헤더와 본문 해시가 같아야 하며, 기존 성공 배포의 plan을 재사용할 수 없습니다.
- `POST /api/worker/rollback-plans/{plan_id}/summary`: 새 rollback plan의 SHA-256과 Terraform diff 요약을 저장합니다. 이 요약과 artifact가 모두 있어야 사용자 승인이 가능합니다.
- `POST /api/worker/deployments/claim`: 대기 중인 AWS 일반 배포 또는 사용자 승인 롤백 작업 하나를 가져옵니다. 롤백 작업에는 `operation_type: "rollback"`, `rollback_from_deployment_id`, `rollback_to_deployment_id`가 포함됩니다. 없으면 `{ "job": null }`.
- `POST /api/worker/deployments/{deployment_id}/events`: 상태와 이벤트를 기록합니다. 일반 배포와 롤백 모두 `queued → provisioning → deploying → healthy` 또는 `failed` 흐름을 사용합니다. 현재 상태와 같은 상태를 보내면 상태와 URL은 그대로 두고 로그 이벤트만 추가합니다. 다른 상태에서 `healthy`로 전이할 때는 HTTP(S) `url`이 필요합니다.
- `POST /api/worker/connections/{connection_id}/complete`: AWS 계정 확인 결과를 연결 상태에 반영합니다.
- `GET /api/worker/connections/pending`: worker 인증 후 처리할 대기 연결 배열을 반환합니다.
- `POST /api/worker/connections/{connection_id}/fail`: worker 실패 상태와 마스킹된 오류를 저장합니다.

worker 이벤트의 `message`와 `details`는 DB 저장 전에 비밀값을 마스킹합니다. `AWS_SECRET_ACCESS_KEY`, `SecretAccessKey`, `SessionToken`처럼 snake_case, kebab-case, camelCase/PascalCase로 표기된 민감 키를 처리하며, 이벤트 및 프로젝트 상태 로그를 조회할 때도 기존 저장 데이터의 값이 다시 노출되지 않도록 마스킹합니다.

`environment: API_KEY=...`, 인용·이스케이프 JSON 문자열 안의 민감 키, 배열·객체 형태의 비밀값도 같은 경계에서 처리합니다. 분석 결과·계획 변수/설명·연결 설정·롤백 diff의 비밀값은 422로 거부하며 `python:3.12` 같은 일반 Docker 태그는 허용합니다.

현재 API는 Terraform, Docker 또는 AWS 명령을 직접 실행하지 않습니다. 실제 worker 구현과 AWS 배포 검증은 별도 작업입니다.

대기열에서 가져온 작업의 Terraform plan 파일이 없거나 해시가 맞지 않으면 해당 배포를 `failed`로 바꾸고 오류 이벤트를 남긴 뒤, 다음 대기 작업을 계속 찾습니다. 하나의 손상된 작업이 나머지 작업을 막지 않도록 처리합니다.

## 공통 오류 예시

```json
{ "error": "승인된 배포 계획만 실행 대기열에 넣을 수 있습니다." }
```

대표 상태 코드는 `400` 잘못된 요청, `401` worker 인증 실패, `404` 리소스 없음, `409` 상태/fingerprint 충돌, `413` 업로드 크기 초과, `415` 미지원 Content-Type, `422` 입력 검증 오류, `503` DB 연결 오류입니다.
