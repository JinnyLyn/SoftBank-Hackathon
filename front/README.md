# Paved Clouds front

현재 제품 기준은 [관리형 배포·독립 도메인](../docs/PRODUCT_DIRECTION.md), ID는 `2026-10-10-managed-domains-v1`이다. 사용자의 AWS 계정·IAM 키·DNS 지식을 전제하지 않는다. 운영자 연결 준비와 사용자 배포 흐름을 분리하는 것이 목표이며, 아래 기존 화면/API 설명을 새 흐름의 완성으로 해석하지 않는다.

소스(zip 또는 GitHub public 저장소)와 사용 규모를 한 화면에서 받음 → 분석·구성 추천·추천 조합 코드까지 미리 생성 → 코드 검토·승인 → 배포.
코드 검토 단계에서는 AI를 돌리지 않고 앞에서 만든 결과를 바로 보여 줌. 추천과 다른 칸을 고르면 고르는 순간 뒤에서 그 조합 코드를 만듦.

제품 배포 대상은 AWS만이다. 새 배포는 사용자 AWS 연결 없이 운영자가 준비한 AWS(관리형)로 진행한다(mock 포함, `QuickConnect` 제거). 사용자 AWS 계정을 연결하는 "연결 관리" 화면과 관련 코드는 뺐다(10/11, 아래 "연결 관리를 뺀 이유"). 온프레미스는 팀이 확정한 제품 범위가 아니다. 다만 발표자 PC의 로컬 Docker로 올리는 **선택 기능**을 실제 백엔드에 연결해 두었고(아래 "온프레미스(로컬 Docker)"), 기본은 꺼져 있다.
React + TypeScript + Vite.

## 도메인 작업의 현재 경계

- 기존 확보 도메인 연결 시연(A)과 신규 구매부터의 자동화(B)는 별개다. 이번 시연의 최종 선택은 [남은 결정](../docs/OPEN_QUESTIONS.md)에서 확인하며, 구매 UI를 만들었다는 이유로 B 전체를 확정하지 않는다.
- 10/10 확인한 개인 브랜치 `JinVibe:f169d27`에는 보유/구매/나중에 화면·MOCK·제안 API가 있다. 그 README는 백엔드·인프라 미구현, 진행 상태는 MOCK이라고 명시한다. 이 문서 수정으로 해당 코드가 main에 통합되지는 않는다.
- 실제 지원한 경로만 사용자에게 완료로 보여준다. API 미지원으로 AWS 기본 주소를 쓰는 경우는 미리보기/도메인 미연결 상태이며, 독립 도메인 연결 성공으로 표시하지 않는다. 수동 DNS 입력 안내는 자동화 완료가 아니다.

## 실행

Node 22.12 이상 필요 (22.11은 경로에 한글이 있으면 크래시함).

```
npm install
npm run dev     # http://localhost:5173
npm run build
```

## 백엔드 연결

- **기본은 실제 백엔드.** 백엔드 없이 화면만 볼 때는 `.env` 에 `VITE_USE_MOCK=true` → `src/api/mock.ts` 의 가짜 응답으로 끝까지 돌아감. 이때 화면 상단에 **MOCK** 띠가 뜨고 배포 결과에도 MOCK 표시가 붙음.
  zip 파일 이름이나 저장소 주소에 `fail` 이 들어가면 헬스체크 실패 + AI 진단 화면이 나옴.
- 배포 대상 화면은 `VITE_PROVIDERS` 로 켜고 끔. 기본은 `aws` 만. `aws,onprem`(또는 `onprem`)은 아래 "온프레미스(로컬 Docker)" 선택 기능을 켠다. 온프레미스는 현재 제품 범위가 아니다.
- 실제 백엔드: dev 서버가 `/api` 를 `http://127.0.0.1:8000` 으로 넘김(다른 주소면 `API_TARGET` 환경 변수). 같은 출처가 되므로 백엔드 CORS 설정이 필요 없음.
- 연동 코드는 `src/api/real.ts` 한 파일. 구현된 계약은 [back/API.md](../back/API.md) 기준이고, 화면 타입(`src/types.ts`)으로 바꾸는 일도 여기서만 함. 아래 "도메인"의 제안 API는 서버 계약과 구분한다.

| 화면 동작 | 백엔드 요청 |
| --- | --- |
| 분석 시작 (ZIP) | `POST /api/projects?name=&filename=&expected_users=&traffic_pattern=&monthly_budget_usd=&purpose=` 본문은 ZIP 원본 바이트, `Content-Type: application/zip` |
| 분석 시작 (GitHub) | `POST /api/projects/github` `{ name, repository_url, ref, expected_users, traffic_pattern, monthly_budget_usd, purpose }` |
| 분석 결과 대기 | `GET /api/projects/{id}/analyses/latest` 를 2초 간격 (404는 대기, 5분 제한) |
| 추천 비교표 | `GET /api/projects/{id}/plans` 의 승인 대기 계획 (2초 간격, 5분 제한). 계획이 아직 없으면 `GET /api/projects/{id}/plan-failures`도 읽어, worker가 `retryable=false` 실패(예산 부족 등)를 알렸으면 바로 그 이유를 보여 줌(404면 예전 백엔드로 보고 계속 기다림). AWS 줄 이름은 "관리형 AWS"로 고정. 계획이 있는 칸은 계획 변수 `tier` 하나이고, 나머지(비교용) 칸을 누르면 `POST /api/projects/{id}/plan-requests`로 그 안의 계획을 다시 요청한 뒤 새 계획을 기다림(다시 승인) |
| 코드 검토 | `GET /api/plans/{id}` — 변수값, 설명, 모듈, 가격 기준, fingerprint, plan 파일 준비 여부 |
| 승인하고 배포 | `POST /api/plans/{id}/approve` `{ expected_fingerprint }` → `POST /api/deployments` `{ plan_id, expected_fingerprint }` (202 = 대기열 등록) |
| 배포 진행 | `GET /api/projects/{id}/status` 0.7초 간격 (404는 대기열 대기로 표시) |
| 배포 이력 | `GET /api/deployments?limit=50` |
| 사용 규모·월 예산 | ZIP은 업로드 쿼리, GitHub는 등록 본문의 `expected_users`·`traffic_pattern`·`monthly_budget_usd`·`purpose`. 백엔드가 저장하고 AI runner가 분석 결과 `scale` 로 복사해 인프라 worker가 구성·예산 검사에 씀 |

### 연결 관리를 뺀 이유

사용자 AWS 계정을 연결하는 화면(CloudFormation 역할 위임)은 10/10 이전, 사용자 계정에 배포하던 설계에서 온 것이다. 지금은 운영자가 준비한 AWS에 배포하고([PRODUCT_DIRECTION.md](../docs/PRODUCT_DIRECTION.md)), 그 연결은 읽기 전용 역할을 확인할 뿐 계획·빌드·배포 어디에서도 쓰이지 않았다. 또 템플릿 공개 주소(`AWS_CONNECTION_TEMPLATE_URL`)와 AWS에서 닿는 백엔드 주소(`PUBLIC_API_BASE_URL`)가 없으면 끝나지 않아 '연결 대기'로만 남았다. 그래서 프런트에서 화면·API 호출·mock을 모두 뺐다.

- 백엔드의 `/api/connections` API와 worker의 연결 확인, `infra/connection/` 템플릿은 그대로 있다(각 담당 범위). 프런트는 이제 부르지 않는다.
- 다시 살리려면 용도(예: 사용자 계정에 배포)와 권한 범위·승인 방식을 팀에서 먼저 정한다. 연결이 끝나는 조건은 [infra/README.md](../infra/README.md#사용자-aws-연결-기존-연결-화면백엔드-호환용) 참고.

### 온프레미스(로컬 Docker) — 선택 기능

`VITE_PROVIDERS=aws,onprem`(또는 `onprem`만)이면 실제 백엔드 모드(`real.ts`)에서 분석 뒤 **AWS와 온프레미스 계획을 한 비교표에** 보여 준다. 온프레미스는 worker를 `python infra/worker/worker.py --targets aws,local_docker`(또는 `local_docker`)로 실행했을 때만 계획이 생긴다([infra/worker/README.md](../infra/worker/README.md)). 백엔드의 `target`은 `local_docker`이고 화면의 `provider`는 `onprem`이다.

- 비교표의 칸은 계획 하나다(고정 id `local-docker`). AWS는 예전과 같다.
- 승인 대상은 `variables.compose_yaml`(코드 검토 화면의 `docker-compose.yml` 탭)이다. 승인 식별자(`fingerprint`)에 그 SHA-256이 들어 있다. plan 파일 업로드가 없어 `terraform_plan_ready`는 항상 `true`다.
- 추천: 월 예산 안에 드는 AWS 계획이 있으면 AWS의 추천 안, 없으면 추가 비용이 없는 온프레미스. 한쪽 계획만 먼저 오면 다른 쪽을 최대 90초 더 기다린 뒤 있는 것만으로 진행한다(`OTHER_TARGET_GRACE_MS`). 켜 둔 대상만 기다린다(기본 AWS만이면 예전과 같다).
- `onprem`만 켜면(AWS 없음) 도메인 단계는 도메인·HTTPS를 지원하지 않는다는 안내를 먼저 보여 준다(선택은 반영되지 않는다). 헤더·안내 문구도 켜 둔 대상을 따른다.
- 접속 주소는 `http://localhost:<포트>`다(도메인·HTTPS 없음). 비용 0에는 전기·회선·PC 사용 비용이 들어 있지 않다고 화면에 적는다.

### 아직 백엔드와 맞춰야 할 것

- **분석 결과(`result`) 형태**: 백엔드가 고정하지 않음. 화면은 `stack`(`[{label, value}]`), `findings`(`[{level: info|warn, title, detail}]`), `evidence`(문자열 또는 `{file, line, text}`)를 읽고, 없으면 최상위 값들을 스택 표로 보여 줌. LLM 담당과 합의 필요.
- **계획 변수 중 화면용 키**: `tier`(lean/balanced/roomy), `recommended`(true면 추천), `headline`, `tradeoff`, `reason`, `resources`(`[{service, spec, monthlyUsd, why}]`). 없으면 비용 순서로 크기를 정하고 계획 전체를 한 줄로 보여 줌.
- **도메인(제안 API, 백엔드·인프라 미구현)**: 선택지는 **자동 주소**(플랫폼 도메인 아래 서버가 정한 주소, Vercel 방식, 기본)와 **가지고 있는 도메인**(후순위). `PUT /api/projects/{id}/domain` `{mode: auto|own, name}`(auto는 `name: null`) → `{mode, name(auto면 서버가 정한 주소), monthly_usd, records, note?}`, `status.domain` = `{state: skipped|waiting_dns|issuing_cert|active|failed, name, message?, records?, url?}`. 404/405면 미리보기 주소(AWS 기본 주소)로 진행하고 안내함. 도메인 연결 전 주소는 항상 "미리보기"로 표시. 신규 구매·"나중에"는 10/10 대책안에 따라 뺌. 주소 발급·DNS·인증서 진행은 mock에서만 동작.
- **분석·계획 생성 시작**: 화면은 결과가 생길 때까지 기다림. 분석은 `ai/runner.py` 가 결과를 등록하고, PR #12의 worker(main `891fd9b`)가 저장된 분석 결과를 읽어 계획을 만듦(`infra/worker/README.md`). 셋을 한 번에 돌린 실제 통합 검증은 별도.
- **plan 요약 개수**: 추가/변경/삭제 개수를 주는 필드가 없어 `-` 로 표시.
- **실패 후 수정**: 수정안 반영 API(`/fix`)가 없어 안내 문구로 실패 처리. `status` 의 `diagnosis` 는 `{cause, fix, patch: {file, before, after}}` 형태일 때 화면에 나옴.

## 보안

- 로그인(SSO)은 범위에서 뺐음. 플랫폼은 발표자 PC에서 실행하는 전제.
- **비밀 값을 받지 않음.** 기존 화면에는 AWS CloudFormation 역할 위임과 온프레미스 공개 키 등록 안내가 있고 액세스 키/SSH 개인 키 입력란은 없다. 역할 위임도 비전공자에게 지식 부담이 있으므로 기본 사용자 흐름의 필수 단계로 유지하지 않는다. 운영자 준비 절차는 [OPERATOR_AWS.md](../docs/OPERATOR_AWS.md)를 참조한다.
- 백엔드가 준 콘솔 주소(`setupUrl`)는 `console.aws.amazon.com` 의 https 주소일 때만 링크로 보여 줌.
- **업로드 제한.** zip 만, 200MB 이하 (프론트에서 먼저 막고 서버에서도 다시 검사).
- **보안 헤더.** `vite.config.ts` 의 `SECURITY_HEADERS`, `CSP` 를 운영 서버에도 똑같이 설정.
  `npm run build && npm run preview` 로 CSP가 적용된 상태를 확인할 수 있음.
