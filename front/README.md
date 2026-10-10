# Paved Clouds front

현재 제품 기준은 [관리형 배포·독립 도메인](../docs/PRODUCT_DIRECTION.md), ID는 `2026-10-10-managed-domains-v1`이다. 사용자의 AWS 계정·IAM 키·DNS 지식을 전제하지 않는다. 운영자 연결 준비와 사용자 배포 흐름을 분리하는 것이 목표이며, 아래 기존 화면/API 설명을 새 흐름의 완성으로 해석하지 않는다.

소스(zip 또는 GitHub public 저장소)와 사용 규모를 한 화면에서 받음 → 분석·구성 추천·추천 조합 코드까지 미리 생성 → 코드 검토·승인 → 배포.
코드 검토 단계에서는 AI를 돌리지 않고 앞에서 만든 결과를 바로 보여 줌. 추천과 다른 칸을 고르면 고르는 순간 뒤에서 그 조합 코드를 만듦.

제품 배포 대상은 AWS만이다. 현재 코드에는 기존 "연결 관리"와 온프레미스 화면이 남아 있다. 연결된 곳이 없으면 AWS 역할 연결을 요구하는 화면은 관리형 흐름으로 전환할 대상이다. 연결 대상 × 구성 크기 비교는 기존 구현 설명이며 사용자 AWS 연결을 필수 제품 요구로 되살리지 않는다.
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

- 기본은 `src/api/mock.ts` 의 가짜 응답으로 끝까지 돌아감. 이때 화면 상단에 **MOCK** 띠가 뜨고 배포 결과에도 MOCK 표시가 붙음.
  zip 파일 이름이나 저장소 주소에 `fail` 이 들어가면 헬스체크 실패 + AI 진단 화면이 나옴.
- 기존 배포 대상 화면은 `VITE_PROVIDERS` 로 켜고 끔. 기본은 `aws` 만. `aws,onprem` 코드가 남아 있어도 온프레미스는 현재 제품 범위 밖이다.
- 실제 백엔드: `.env` 에 `VITE_USE_MOCK=false`. dev 서버가 `/api` 를 `http://127.0.0.1:8000` 으로 넘김(다른 주소면 `API_TARGET` 환경 변수). 같은 출처가 되므로 백엔드 CORS 설정이 필요 없음.
- 연동 코드는 `src/api/real.ts` 한 파일. 구현된 계약은 [back/API.md](../back/API.md) 기준이고, 화면 타입(`src/types.ts`)으로 바꾸는 일도 여기서만 함. 개인 브랜치의 제안 API는 서버 계약과 구분한다.

| 화면 동작 | 백엔드 요청 |
| --- | --- |
| 연결 목록 / 추가 / 이름 수정 / 삭제 | `GET/POST /api/connections`, `PUT/DELETE /api/connections/{id}` (본문은 `provider`, `name`, `fields` 만) |
| 연결 확인 | `POST /api/connections/{id}/check` — 저장된 상태를 다시 읽음. 실제 AWS 확인은 worker가 기록 |
| 분석 시작 (ZIP) | `POST /api/projects?name=&filename=` 본문은 ZIP 원본 바이트, `Content-Type: application/zip` |
| 분석 시작 (GitHub) | `POST /api/projects/github` `{ name, repository_url, ref }` |
| 분석 결과 대기 | `GET /api/projects/{id}/analyses/latest` 를 2초 간격 (404는 대기, 5분 제한) |
| 추천 비교표 | `GET /api/projects/{id}/plans` 의 승인 대기 계획 + `GET /api/connections` (2초 간격, 5분 제한) |
| 코드 검토 | `GET /api/plans/{id}` — 변수값, 설명, 모듈, 가격 기준, fingerprint, plan 파일 준비 여부 |
| 승인하고 배포 | `POST /api/plans/{id}/approve` `{ expected_fingerprint }` → `POST /api/deployments` `{ plan_id, expected_fingerprint }` (202 = 대기열 등록) |
| 배포 진행 | `GET /api/projects/{id}/status` 0.7초 간격 (404는 대기열 대기로 표시) |
| 배포 이력 | `GET /api/deployments?limit=50` |

### 아직 백엔드와 맞춰야 할 것

- **분석 결과(`result`) 형태**: 백엔드가 고정하지 않음. 화면은 `stack`(`[{label, value}]`), `findings`(`[{level: info|warn, title, detail}]`), `evidence`(문자열 또는 `{file, line, text}`)를 읽고, 없으면 최상위 값들을 스택 표로 보여 줌. LLM 담당과 합의 필요.
- **계획 변수 중 화면용 키**: `tier`(lean/balanced/roomy), `recommended`(true면 추천), `headline`, `tradeoff`, `reason`, `resources`(`[{service, spec, monthlyUsd, why}]`). 없으면 비용 순서로 크기를 정하고 계획 전체를 한 줄로 보여 줌.
- **사용 규모·월 예산 전달**: main의 백엔드는 Issue #17 후속으로 저장 필드를 제공한다(`back/API.md`). 프런트 전달 여부는 실제 사용하는 브랜치에서 확인한다. 10/10 `JinVibe:f169d27`은 ZIP/GitHub 전달을 반영했으나 해당 변경의 main 통합과 실제 연동은 별도다.
- **분석·계획 생성 시작**: 화면은 결과가 생길 때까지 기다림. PR #12의 worker는 main `891fd9b`에 통합됐고 저장된 분석 결과를 읽어 계획을 만듦(`infra/worker/README.md`). 분석 담당 모듈의 결과 등록·실제 통합 검증은 별도 확인.
- **plan 요약 개수**: 추가/변경/삭제 개수를 주는 필드가 없어 `-` 로 표시.
- **실패 후 수정**: 수정안 반영 API(`/fix`)가 없어 안내 문구로 실패 처리. `status` 의 `diagnosis` 는 `{cause, fix, patch: {file, before, after}}` 형태일 때 화면에 나옴.

## 보안

- 로그인(SSO)은 범위에서 뺐음. 플랫폼은 발표자 PC에서 실행하는 전제.
- **비밀 값을 받지 않음.** 기존 화면에는 AWS CloudFormation 역할 위임과 온프레미스 공개 키 등록 안내가 있고 액세스 키/SSH 개인 키 입력란은 없다. 역할 위임도 비전공자에게 지식 부담이 있으므로 기본 사용자 흐름의 필수 단계로 유지하지 않는다. 운영자 준비 절차는 [OPERATOR_AWS.md](../docs/OPERATOR_AWS.md)를 참조한다.
- 백엔드가 준 콘솔 주소(`setupUrl`)는 `console.aws.amazon.com` 의 https 주소일 때만 링크로 보여 줌.
- **업로드 제한.** zip 만, 200MB 이하 (프론트에서 먼저 막고 서버에서도 다시 검사).
- **보안 헤더.** `vite.config.ts` 의 `SECURITY_HEADERS`, `CSP` 를 운영 서버에도 똑같이 설정.
  `npm run build && npm run preview` 로 CSP가 적용된 상태를 확인할 수 있음.
