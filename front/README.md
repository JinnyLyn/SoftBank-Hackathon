# Paved Clouds front

소스(zip 또는 GitHub public 저장소)와 사용 규모를 한 화면에서 받음 → 분석·구성 추천·추천 조합 코드까지 미리 생성 → 코드 검토·승인 → 배포.
코드 검토 단계에서는 AI를 돌리지 않고 앞에서 만든 결과를 바로 보여 줌. 추천과 다른 칸을 고르면 고르는 순간 뒤에서 그 조합 코드를 만듦.

배포 대상은 벤더 중립. AWS, Google Cloud, Azure, 온프레미스(SSH + Docker)를 등록해 두면
분석할 때 연결된 모든 대상 × 구성 크기(작게 시작 / 권장 / 여유 있게)의 비용을 한 표로 비교하고 AI가 하나를 추천함.
대상 종류 추가는 `src/providers.ts` 에 폼 정의만 넣으면 됨.
React + TypeScript + Vite.

## 실행

Node 22.12 이상 필요 (22.11은 경로에 한글이 있으면 크래시함).

```
npm install
npm run dev     # http://localhost:5173
npm run build
```

## 백엔드 연결

- 기본은 `src/api/mock.ts` 의 가짜 응답으로 끝까지 돌아감.
  zip 파일 이름이나 저장소 주소에 `fail` 이 들어가면 헬스체크 실패 + AI 진단 화면이 나옴.
- `.env` 에 `VITE_USE_MOCK=false` 를 넣으면 `/api` 로 요청하고, dev 서버가 `localhost:8000` 으로 프록시함.
- 엔드포인트는 `src/api/index.ts`, 응답 형태는 `src/types.ts` 에 있음. 아직 가안이라 백엔드와 맞춰야 함.

| 화면 동작 | 요청 |
| --- | --- |
| 배포 대상 목록 / 추가 / 수정 / 삭제 | `GET /api/connections`, `POST /api/connections`, `PUT /api/connections/:id`, `DELETE /api/connections/:id` |
| 연결 다시 확인 | `POST /api/connections/:id/check` → `Connection` |
| (AWS) 연결 방식 | 저장하면 `status: "pending"` + `setupUrl`(CloudFormation 빠른 생성 주소)을 돌려줌. 사용자가 콘솔에서 스택을 만들면 `check` 에서 `connected` 와 계정 ID로 바뀜. Role ARN, 리전 입력 없음 |
| (온프레미스) 연결 방식 | 이름만 받아 저장하면 `status: "pending"` + `installCommand`(일회용 토큰이 든 설치 명령 한 줄) + `expiresAt`(10분)을 돌려줌. 사용자가 서버에서 실행하면 스크립트가 Docker 설치, `deploy` 사용자 생성, 공개 키 등록 후 서버 사양을 보고함 → 화면이 3초마다 `check` 해서 `connected` 로 바뀜. 만료되면 같은 id로 다시 저장해 새 명령 발급. 스크립트 내용은 `src/providers.ts` 의 `INSTALL_SCRIPT_PREVIEW` |
| 분석 | `POST /api/projects` (multipart: `file` 또는 `repo_url`+`branch`, `expected_users`, `traffic_pattern`, `purpose`) → `Analysis` |
| 구성 추천 | `POST /api/projects/:id/recommend` → `Recommendation` (연결된 대상별 `options`, 추천 `{connectionId, tier}`, 미리 만든 코드 `bundles["connectionId:tier"]` — 최소한 추천 조합은 포함) |
| 코드 생성 | `POST /api/projects/:id/code` (`{ connectionId, tier }`) → `TerraformBundle` (클라우드는 terraform, 온프레미스는 compose) |
| 승인 | `POST /api/projects/:id/deploy` (`{ connectionId, tier }`) |
| 진행 상태 | `GET /api/projects/:id/status` → `DeployStatus` (로그, URL, 실패 시 진단) — 0.7초 간격 폴링 |
| 배포 이력 | `GET /api/deployments` → `DeployRecord[]` |

## 인증과 보안

- **SSO 전용 로그인.** 회사 이메일 → `POST /api/auth/sso/discover` 로 조직의 IdP(OIDC 또는 SAML)를 찾고,
  백엔드가 준 `redirectUrl` 로 이동. IdP 콜백과 토큰 검증은 백엔드가 하고, 끝나면 `/` 로 돌려보냄.
  - 개인 메일 도메인은 거절.
  - `redirectUrl` 은 같은 출처이거나 `VITE_IDP_HOSTS`(쉼표 구분)에 있는 https 호스트만 허용 (오픈 리다이렉트 방지).
- **세션은 HttpOnly 쿠키.** 프론트는 토큰을 localStorage 등에 저장하지 않음. 앱 시작 시 `GET /api/auth/session` → `{ user, csrfToken }` (없으면 401).
  백엔드 쿠키 설정: `HttpOnly; Secure; SameSite=Lax`, 세션 만료는 서버에서도 관리.
- **CSRF.** GET 이 아닌 요청에는 `X-CSRF-Token` 헤더를 붙임 (`src/api/http.ts`). 백엔드는 세션의 토큰과 비교.
- **401 / 403.** 어떤 요청이든 401이면 로그인 화면으로, 403이면 "권한 없음" 메시지.
- **무활동 30분 자동 로그아웃** (`POST /api/auth/logout`).
- **권한.** `admin` 만 배포 대상 추가·수정·삭제. 배포 승인은 로그인한 누구나 하되 승인자가 이력에 남음(`approvedBy`).
  화면에서 버튼을 숨기는 건 편의일 뿐이고, 실제 차단은 백엔드에서 해야 함.
- **비밀 값을 받지 않음.** 클라우드는 역할 위임(AWS Role + 외부 ID, GCP Workload Identity, Azure 페더레이션),
  온프레미스는 우리 공개 키를 서버에 등록하는 방식. 액세스 키나 SSH 개인 키 입력란이 없음.
- **업로드 제한.** zip 만, 200MB 이하 (프론트에서 먼저 막고 서버에서도 다시 검사).
- **보안 헤더.** `vite.config.ts` 의 `SECURITY_HEADERS`, `CSP` 를 운영 서버에도 똑같이 설정.
  `npm run build && npm run preview` 로 CSP가 적용된 상태를 확인할 수 있음.

mock 모드에서는 아무 회사 메일로 로그인됨. 이메일에 `+member` 를 넣으면(예: `kim+member@company.com`) 일반 구성원 권한.
