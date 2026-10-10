# 백엔드 API 연동 문서

프런트엔드와 백엔드 연동을 위한 현재 API 계약입니다. 구현의 상세 스키마는 실행 중인 FastAPI 문서(`/docs`)와 OpenAPI JSON(`/openapi.json`)을 기준으로 합니다.

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

`POST /api/projects?name={프로젝트명}&filename={파일명}`

- Body: ZIP 원본 바이트
- Header: `Content-Type: application/zip` (또는 `application/octet-stream`)
- 파일은 압축 해제하지 않고 저장합니다. 기본 최대 크기는 200 MiB이며 서버 설정으로 조정할 수 있습니다.

#### GitHub 저장소 등록

`POST /api/projects/github`

```json
{
  "name": "sample-app",
  "repository_url": "https://github.com/owner/repository",
  "ref": "main"
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
  "created_at": "2026-10-10T00:00:00Z"
}
```

GitHub 입력이면 `source_type`은 `github`이고 URL/ref가 채워집니다.

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
- `GET /api/deployments/{deployment_id}`: 배포 상세와 이벤트 목록.

상세 응답과 배포 생성 응답의 내부 상태는 `queued`, `provisioning`, `deploying`, `healthy`, `failed`, `rolling_back`, `rolled_back`입니다. 프로젝트 status endpoint와 목록의 프런트 요약 상태 `running/success/failed`는 내부 상태를 단순화한 값입니다. 성공 시 공개 URL은 `url`에 들어갑니다.

## AWS 연결 API

- `GET /api/connections`: AWS 연결 목록
- `POST /api/connections`: `{ "provider": "aws", "name": "연결 이름", "fields": {} }`로 연결 요청 생성
- `PUT /api/connections/{connection_id}`: 연결 이름/설정 수정
- `POST /api/connections/{connection_id}/check`: 저장된 연결 상태 조회. 현재 AWS 자격 증명을 직접 호출해 실시간 검증하는 endpoint는 아닙니다.
- `DELETE /api/connections/{connection_id}`: 연결 삭제

연결 생성 결과에는 `status: "pending"`과 설정용 `setupUrl`이 포함될 수 있습니다. CloudFormation 템플릿 URL 설정과 연결 완료 callback은 서버 설정/worker 연동이 필요합니다. API가 프런트 요청만으로 AWS 계정을 검증하지는 않습니다.

## Worker 전용 API

아래 경로는 프런트 호출용이 아닙니다. 모든 요청에 `X-Worker-Token: {WORKER_API_TOKEN}`이 필요합니다.

- `POST /api/worker/plans/{plan_id}/terraform-plan`: `Content-Type: application/octet-stream`으로 Terraform binary plan 업로드. 서버가 SHA-256을 확인합니다.
- `POST /api/worker/deployments/claim`: 대기 중인 AWS 작업 하나를 가져옵니다. 없으면 `{ "job": null }`.
- `POST /api/worker/deployments/{deployment_id}/events`: 상태와 이벤트를 기록합니다. 허용되는 진행은 `queued → provisioning → deploying → healthy`이며 실패/롤백 상태 전이도 제한적으로 허용합니다. 현재 상태와 같은 상태를 보내면 상태와 URL은 그대로 두고 로그 이벤트만 추가합니다. 다른 상태에서 `healthy`로 전이할 때는 HTTP(S) `url`이 필요합니다.
- `POST /api/worker/connections/{connection_id}/complete`: AWS 계정 확인 결과를 연결 상태에 반영합니다.

현재 API는 Terraform, Docker 또는 AWS 명령을 직접 실행하지 않습니다. 실제 worker 구현과 AWS 배포 검증은 별도 작업입니다.

대기열에서 가져온 작업의 Terraform plan 파일이 없거나 해시가 맞지 않으면 해당 배포를 `failed`로 바꾸고 오류 이벤트를 남긴 뒤, 다음 대기 작업을 계속 찾습니다. 하나의 손상된 작업이 나머지 작업을 막지 않도록 처리합니다.

## 공통 오류 예시

```json
{ "error": "승인된 배포 계획만 실행 대기열에 넣을 수 있습니다." }
```

대표 상태 코드는 `400` 잘못된 요청, `401` worker 인증 실패, `404` 리소스 없음, `409` 상태/fingerprint 충돌, `413` 업로드 크기 초과, `415` 미지원 Content-Type, `422` 입력 검증 오류, `503` DB 연결 오류입니다.
