# Paved Clouds 백엔드

배포 플랫폼의 Python API와 MySQL 영속성 계층입니다. 프로젝트 ZIP/GitHub 소스 메타데이터, LLM 분석 결과, 승인된 배포 계획, 배포 상태와 이벤트 로그를 저장합니다. 제품 LLM 호출과 AWS 배포 실행기는 이 서비스에 포함하지 않습니다. 새 배포는 AWS 클라우드만 지원하며, 작업자가 연결되기 전에는 배포가 `queued` 상태에 머뭅니다.

## 실행 환경

- Python `3.13.16` (`.python-version`)
- FastAPI `0.142.4`
- Uvicorn standard `0.54.0`
- PyMySQL `1.1.2`
- cryptography `46.0.5`
- MySQL `8.4.11` (8.4 LTS; Compose image is pinned to this version)

### 로컬 Docker 개발 환경

Docker Compose는 개발 PC에서 백엔드 API와 MySQL을 실행하는 용도입니다. 제품 배포 대상은 AWS 클라우드이며 온프레미스 배포는 지원하지 않습니다. API는 개발 PC의 `127.0.0.1:8000`에만 공개하고, MySQL, 업로드 소스 ZIP, Terraform plan artifact는 named volume에 보존합니다.

```powershell
docker compose up -d --build db
docker compose run --rm api python -m app.cli migrate
docker compose up -d --build api
```

API 문서는 FastAPI가 제공하는 `http://127.0.0.1:8000/docs`와 `/openapi.json`에서 확인할 수 있습니다. 프런트 연동을 위한 요청·응답 계약과 흐름은 [`API.md`](API.md)를 참고하세요. 생존 확인은 `/health`, DB 연결 준비 확인은 `/ready`입니다. AWS/RDS 배포 시 Compose의 개발 계정 대신 비밀 저장소에서 주입한 `DATABASE_URL`을 사용하고, 업로드 디렉터리는 영속 저장소로 연결해야 합니다.

로컬 작업자가 worker API를 사용할 때는 Compose를 실행하는 셸에 `WORKER_API_TOKEN`을 설정합니다. 값은 저장소에 기록하지 말고, worker에 같은 값을 `X-Worker-Token`으로 전달합니다. 별도 origin의 프런트를 연결하면 `CORS_ORIGINS`에 정확한 origin 목록을 쉼표로 구분해 설정합니다. 기본값은 CORS 비활성화입니다.

GitHub 저장소 입력은 공개 저장소는 별도 설정 없이 사용할 수 있습니다. 비공개 저장소를 가져올 때는 저장소 읽기 권한만 부여한 토큰을 `GITHUB_TOKEN` 환경 변수로 주입합니다. 토큰을 Compose 파일이나 저장소에 직접 적지 마세요.

### 직접 실행

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DATABASE_URL = "mysql://user:password@127.0.0.1:3306/paved_clouds"
$env:UPLOAD_DIR = "./data/uploads"
python -m app.cli migrate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`DATABASE_URL`은 `mysql://user:password@host:3306/database` 또는 `mysql+pymysql://...` 형식입니다. 비밀번호에 `@`, `:`, `/`, `%` 등이 있으면 URL 인코딩해야 합니다. AWS RDS 연결에서 TLS CA 검증이 필요하면 `DB_SSL_CA`에 AWS RDS CA bundle 경로를 설정합니다. 앱 시작 시 테이블을 자동 생성하지 않습니다. 마이그레이션은 별도 명령으로 실행하고 `schema_migrations`에 적용 기록을 남깁니다.

## 구현된 API

모든 API 응답과 오류 본문은 JSON입니다. 시간은 UTC 기반 MySQL timestamp로 저장합니다.

| 메서드 | 경로 | 기능 |
|---|---|---|
| `GET` | `/health` | DB와 독립적인 liveness 확인 |
| `GET` | `/ready` | MySQL 연결 확인 |
| `GET` | `/api/connections` | 저장된 AWS 연결 목록 |
| `POST` | `/api/connections` | AWS 연결 요청 저장 및 CloudFormation 콘솔 링크 생성 |
| `PUT` | `/api/connections/{id}` | AWS 연결 이름/설정 변경 |
| `POST` | `/api/connections/{id}/check` | 현재 연결 상태 확인 |
| `DELETE` | `/api/connections/{id}` | AWS 연결 설정 삭제 |
| `POST` | `/api/projects?name=...&filename=...` | `application/zip` 원문 업로드, SHA-256 계산, 프로젝트 메타데이터 저장 |
| `POST` | `/api/projects/github` | GitHub 저장소 URL과 선택 ref에서 ZIP을 가져와 같은 검증 후 저장 |
| `GET` | `/api/projects?limit=20&cursor=...` | 커서 기반 프로젝트 목록 |
| `GET` | `/api/projects/{id}` | 프로젝트 상세 |
| `POST` | `/api/projects/{id}/analyses` | LLM 담당 모듈이 만든 버전·입력 해시 포함 분석 결과 저장 |
| `GET` | `/api/projects/{id}/analyses/latest` | 최신 분석 결과 |
| `POST` | `/api/plans` | 대상, Terraform 모듈/변수, 설명, 비용 추정치로 배포 계획 저장 |
| `GET` | `/api/projects/{id}/plans` | 프로젝트 배포 계획 목록 |
| `GET` | `/api/plans/{id}` | 배포 계획과 fingerprint 조회 |
| `POST` | `/api/plans/{id}/approve` | 표시된 fingerprint가 일치하는 대기 계획만 승인 |
| `POST` | `/api/deployments` | 승인된 fingerprint를 다시 확인하고 배포 작업을 대기열에 등록 |
| `GET` | `/api/deployments?project_id=...` | 배포 이력 목록 |
| `GET` | `/api/deployments/{id}` | 배포 상태와 이벤트 이력 |
| `GET` | `/api/deployments/{id}/rollback-candidate` | 실패 배포의 사용자 승인 롤백 가능 여부와 이전 정상 버전 조회 |
| `POST` | `/api/deployments/{id}/rollback` | 사용자가 확인한 이전 `healthy` 버전으로 롤백 작업 등록 |
| `GET` | `/api/projects/{id}/status` | 프런트용 최근 배포 상태·로그 조회 |
| `POST` | `/api/worker/deployments/claim` | 인증된 작업자가 대기 작업을 원자적으로 가져옴 |
| `POST` | `/api/worker/deployments/{id}/events` | 인증된 작업자의 상태 전이·이벤트 기록 |
| `POST` | `/api/worker/plans/{id}/terraform-plan` | 인증된 작업자가 SHA-256을 검증한 binary plan을 저장 |

ZIP과 GitHub 아카이브는 압축 해제하지 않고 보관합니다. 업로드 크기 기본 한도는 200 MiB이며 `MAX_UPLOAD_BYTES`로 조정할 수 있습니다. ZIP 경로 탈출, 심볼릭 링크, 암호화 ZIP, 과도한 압축 크기/압축률, 파일 수를 검사합니다. 원본은 DB에 넣지 않고 `UPLOAD_DIR`에 저장하며 DB에는 출처 유형, GitHub URL/ref(해당 시), 해시와 경로를 둡니다. GitHub 입력은 `https://github.com/{owner}/{repo}` 형식만 허용합니다.

계획은 저장 시 정규 JSON으로 fingerprint를 계산합니다. AWS 계획은 비용 추정치와 저장된 Terraform plan의 SHA-256이 필요합니다. 작업자가 `/api/worker/plans/{id}/terraform-plan`으로 binary plan을 올리면 백엔드는 해시를 확인하고 별도 비공개 디렉터리에 저장합니다. 승인 전, 배포 등록 전, 작업자에게 전달하기 전에 파일 해시를 다시 확인합니다. 승인 요청이 받은 fingerprint와 DB의 값이 다르면 거부하고, 배포 등록 때에도 승인 당시 값과 다시 비교합니다. 승인은 한 번의 배포 등록에만 사용할 수 있습니다. 계획을 바꾸거나 재시도하려면 새 계획을 만들고 다시 승인해야 합니다. API는 승인만 기록하고 Terraform/Docker/AWS 명령을 실행하지 않습니다.

작업자 API는 `WORKER_API_TOKEN`이 설정되어야 사용할 수 있으며 `X-Worker-Token` 헤더를 비교합니다. 일반 배포와 롤백 작업 모두 `queued → provisioning → deploying → healthy` 또는 `failed`의 제한된 전이를 따릅니다. 롤백은 실패한 원래 배포를 바꾸지 않고, 별도 `operation_type: rollback` 이력으로 실행합니다. 로그의 흔한 credential 패턴은 저장 전에 마스킹합니다. 호출 측에서도 로그에 비밀을 보내지 않아야 합니다.

첫 AWS 배포가 실패하면 이전 `healthy` 배포가 없으므로 롤백을 제공하지 않습니다. 프런트는 `rollback-candidate` 응답의 `first_deployment` 또는 `no_previous_healthy` 이유를 표시하고, AI 실패 원인 분석·수정안 → 새 계획·비용·plan 확인 → 사용자 승인 → 재배포 흐름으로 진행해야 합니다. 이전 배포가 있는 실패에서는 사용자가 롤백을 선택했을 때만 `rollback` endpoint를 호출합니다. 백엔드는 사용자가 화면에서 확인한 직전 `healthy` 배포 ID와 현재 후보가 일치하는지, 저장 Terraform plan의 SHA-256이 유효한지를 다시 검사합니다. 자동 롤백은 수행하지 않습니다.

연결 API는 AWS만 지원합니다. CloudFormation 링크를 표시하려면 템플릿을 공개 HTTPS 주소에 배포하고 `AWS_CONNECTION_TEMPLATE_URL`을 설정합니다. 연결 상태는 검증된 AWS 계정 확인 주체가 `POST /api/worker/connections/{id}/complete`로 계정 ID를 보고할 때 `connected`가 됩니다. `WORKER_API_TOKEN` 없이 연결 완료를 호출할 수 없습니다.

## DB 테이블

- `projects`: ZIP/GitHub 출처, 원본 경로·크기·SHA-256
- `analyses`: 분석 스키마 버전, 원본 해시, JSON 결과
- `deployment_plans`: 대상, 모듈 변수, 비용 추정치, plan digest, 승인 fingerprint와 시각
- `deployments`: 일반 배포·사용자 승인 롤백별 실행 상태와 URL, 원래 실패 배포/복구 기준 정상 배포 연결
- `deployment_events`: 추가 전용 상태/로그 이력
- `schema_migrations`: 적용한 SQL 마이그레이션 버전

비밀 키처럼 보이는 JSON 필드는 분석 결과와 Terraform 변수에서 거부합니다. 키가 평문인 환경 변수, DB URL, API 자격 증명은 코드·분석 결과·계획 변수·로그에 저장하지 마세요. 업로드 디렉터리와 MySQL 데이터 볼륨은 서로 별도로 백업해야 합니다.

## 현재 연결 경계

- LLM 분석은 이 API에 분석 JSON을 기록하는 방식으로 연결합니다. 모델, endpoint, 프롬프트, 분석 JSON의 필드 스키마는 이 백엔드에서 고정하지 않았습니다.
- PR #7 프런트는 분석·추천·코드 생성 API를 예상하지만, LLM 분석 JSON 및 추천 번들 계약은 아직 연결되지 않았습니다. `VITE_USE_MOCK=false` 전환 전 이 계약을 확정해야 합니다.
- 배포 worker는 AWS 작업과 사용자 승인 롤백 작업을 claim하고 이벤트를 보고할 계약을 갖습니다. 롤백 작업에는 이전 `healthy` 배포의 검증된 Terraform plan과 원래 실패/복구 기준 배포 ID가 전달됩니다. AWS Terraform 모듈 입력/출력과 DB 마이그레이션 하위 호환성은 인프라 담당과 합의한 뒤 worker에서 구현해야 합니다. Compose는 개발용 API/MySQL 실행에만 사용합니다.
- 인증된 작업자 API 외의 제품 사용자 인증·인가, 승인자 신원, 브라우저 CORS 도메인은 아직 팀 계약으로 확정되지 않았습니다. 이 API를 공개 ALB에 직접 노출하지 말고, 접근 경계를 합의한 후 배포해야 합니다.
- DB 스키마는 수동 실행 마이그레이션으로 제공했습니다. 시작 시 자동 DDL을 적용하지 않으며, 운영 DB에 적용하기 전 백업과 마이그레이션 실행 주체를 정해야 합니다.
