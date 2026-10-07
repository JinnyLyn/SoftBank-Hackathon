# OneShip front

zip 업로드 → AI 분석 → 비용 추정 → terraform plan 승인 → 배포 진행 화면.
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
  zip 파일 이름에 `fail` 이 들어가면 헬스체크 실패 + AI 진단 화면이 나옴.
- `.env` 에 `VITE_USE_MOCK=false` 를 넣으면 `/api` 로 요청하고, dev 서버가 `localhost:8000` 으로 프록시함.
- 엔드포인트는 `src/api/index.ts`, 응답 형태는 `src/types.ts` 에 있음. 아직 가안이라 백엔드와 맞춰야 함.

| 화면 동작 | 요청 |
| --- | --- |
| 분석 시작 | `POST /api/projects` (multipart: file, expected_users, purpose) → `Analysis` |
| 비용 추정 | `POST /api/projects/:id/estimate` → `CostLine[]` |
| plan 조회 | `POST /api/projects/:id/plan` → `PlanSummary` |
| 승인 | `POST /api/projects/:id/deploy` |
| 진행 상태 | `GET /api/projects/:id/status` → `DeployStatus` (0.8초 간격 폴링) |
| 배포 이력 | `GET /api/deployments` → `DeployRecord[]` |
