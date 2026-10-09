# Paved Clouds front

소스(zip 또는 GitHub public 저장소)와 사용 규모를 한 화면에서 받음 → 분석·구성 추천·추천 조합 코드까지 미리 생성 → 코드 검토·승인 → 배포.
코드 검토 단계에서는 AI를 돌리지 않고 앞에서 만든 결과를 바로 보여 줌. 추천과 다른 칸을 고르면 고르는 순간 뒤에서 그 조합 코드를 만듦.

배포 대상은 AWS와 온프레미스(SSH + Docker) 두 종류. "연결 관리" 탭에서 등록하거나, 연결된 곳이 없으면 새 배포 첫 화면에서 바로 연결함. 등록해 두면
분석할 때 연결된 모든 대상 × 구성 크기(작게 시작 / 권장 / 여유 있게)의 비용을 한 표로 비교하고 AI가 하나를 추천함.
React + TypeScript + Vite.

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
- `.env` 에 `VITE_USE_MOCK=false` 를 넣으면 `/api` 로 요청하고, dev 서버가 `localhost:8000` 으로 프록시함.
- 엔드포인트는 `src/api/index.ts`, 응답 형태는 `src/types.ts` 에 있음. 아직 가안이라 백엔드와 맞춰야 함.

| 화면 동작 | 요청 |
| --- | --- |
| 배포 대상 목록 / 추가 / 수정 / 삭제 | `GET /api/connections`, `POST /api/connections`, `PUT /api/connections/:id`, `DELETE /api/connections/:id` |
| 연결 다시 확인 | `POST /api/connections/:id/check` → `Connection` |
| (AWS) 연결 방식 | 저장하면 `status: "pending"` + `setupUrl`(CloudFormation 빠른 생성 주소)을 돌려줌. 사용자가 콘솔에서 스택을 만들면 `check` 에서 `connected` 와 계정 ID로 바뀜. Role ARN, 리전 입력 없음 |
| (온프레미스) 연결 방식 | 이름만 받아 저장하면 `status: "pending"` + `installCommand`(일회용 토큰이 든 설치 명령 한 줄) + `expiresAt`(10분)을 돌려줌. 사용자가 서버에서 실행하면 스크립트가 Docker 설치, `deploy` 사용자 생성, 공개 키 등록 후 서버 사양을 보고함 → 화면이 3초마다 `check` 해서 `connected` 로 바뀜. 만료되면 같은 id로 다시 저장해 새 명령 발급. 스크립트 내용은 `src/providers.ts` 의 `INSTALL_SCRIPT_PREVIEW` |
| 분석 | `POST /api/projects` (multipart: `file` 또는 `repo_url`+`branch`, `expected_users`, `traffic_pattern`, `purpose`, `monthly_budget_usd`) → `Analysis` |
| 구성 추천 | `POST /api/projects/:id/recommend` → `Recommendation` (연결된 대상별 `options`, 추천 `{connectionId, tier}`, 미리 만든 코드 `bundles["connectionId:tier"]` — 최소한 추천 조합은 포함). 월 예산을 넘는 칸은 화면에서 고를 수 없음. 예산 안에 맞는 구성이 없으면 `recommended: null` + `reason` 에 이유 |
| 코드 생성 | `POST /api/projects/:id/code` (`{ connectionId, tier }`) → `TerraformBundle` (AWS는 terraform, 온프레미스는 compose) |
| 승인 | `POST /api/projects/:id/deploy` (`{ connectionId, tier }`) |
| 실패 후 수정 | `POST /api/projects/:id/fix` (`{ connectionId, tier }`) → 수정안을 반영해 검증·plan을 다시 만든 `TerraformBundle` (`patches` 에 바뀐 내용). 화면은 코드 검토로 돌아가 **다시 승인**받은 뒤에만 `deploy` 호출 |
| 진행 상태 | `GET /api/projects/:id/status` → `DeployStatus` (로그, URL, 실패 시 진단) — 0.7초 간격 폴링 |
| 배포 이력 | `GET /api/deployments` → `DeployRecord[]` |

## 보안

- 로그인(SSO)은 범위에서 뺐음. 플랫폼은 발표자 PC에서 실행하는 전제.
- **비밀 값을 받지 않음.** AWS는 CloudFormation 스택으로 만든 역할 위임(외부 ID), 온프레미스는 우리 공개 키를 서버에 등록하는 설치 명령.
  액세스 키나 SSH 개인 키 입력란이 없음.
- 백엔드가 준 콘솔 주소(`setupUrl`)는 `console.aws.amazon.com` 의 https 주소일 때만 링크로 보여 줌.
- **업로드 제한.** zip 만, 200MB 이하 (프론트에서 먼저 막고 서버에서도 다시 검사).
- **보안 헤더.** `vite.config.ts` 의 `SECURITY_HEADERS`, `CSP` 를 운영 서버에도 똑같이 설정.
  `npm run build && npm run preview` 로 CSP가 적용된 상태를 확인할 수 있음.
