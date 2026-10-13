# on-prem 서비스 회귀 경계

- 일반 `/onprem`은 MOCK이며 readiness·파일 업로드·분석·승인·배포 API를 전혀 호출하지 않는다. 잘못된 파일·빈 소스를 거부하고 유효 ZIP/GitHub만 진행한다. 입력 변경은 검토·승인을 폐기하며 체크박스 승인 전 모의 배포를 시작하지 않는다. 완료 후 재시작·일본어·모바일 표시와 가짜 접속 URL 없음은 `node scripts/ci/test_onprem_mock.mjs --url http://127.0.0.1:15174`로 확인한다.
- 아래 실제 화면 회귀는 `VITE_ONPREM_ONLY=true VITE_USE_MOCK=false`로 실행한 Vite에 적용한다. 기본 MOCK을 실제 Docker 성공으로 해석하지 않는다.

- AWS `/`의 화면·기본 provider·기존 API 동작은 유지한다. `/onprem`은 AWS 연결/도메인 요청 없이 로컬 계획만 선택한다.
- on-prem 입력은 ZIP/GitHub 소스만 받는다. 규모·접속 패턴·예산·용도 입력과 드롭다운이 없고, ZIP 쿼리/GitHub JSON 모두 동일한 로컬 기본값(`~100`/`steady`/예산 0, 용도 없음)을 전송한다. 소스 전환·새 분석 후에도 자동 승인하지 않으며 양수·음수·잘못된 계획 금액은 승인을 차단한다.
- 1번 분석 버튼과 2번 결과 제목은 “구성 분석”으로 표시한다. 3번 코드·주석·문자열·줄 번호는 배경 대비 4.5:1 이상, 글자 13px 이상과 충분한 줄 간격을 유지하고 요약의 줄바꿈을 보존한다. 색상 변경은 `.onprem` 내부에만 적용한다.
- 다른 프로젝트·다른 plan·fingerprint·target과 승인 전후 변경을 거부한다. 같은 프로젝트의 역순 추천 응답, 초기화 후 늦은 응답, AWS 공유 cache 변경도 기존 검토를 바꾸지 못한다.
- 실행기 종료·프로세스 종료·API 오류를 삼킨 0건 반환은 준비 성공이 아니다. 빈 API 목록의 실제 성공은 성공 관측이다. busy 기한은 API 성공/heartbeat로 늘어나지 않는다.
- 없는 설정/파일, 잘못된 JSON/schema/run-id, 미래/만료 시각, DB 실패는 non-ready다. 준비 실패 시 입력/검토는 유지하고 새 분석·승인만 차단한다. 이미 등록된 작업 조회는 지속한다.
- 고정 Docker endpoint, dotenv opt-out, mock/외부 API 환경 오염 차단, 포트 충돌, 중복 실행, 일부 시작 실패와 정확한 child process group 정리를 검사한다.
- 단위 검사: `uv run --no-project --python 3.12 python -m unittest discover -s scripts -p 'test_onprem_*.py' -v`, `PYTHONPATH=back uv run --no-project --python 3.12 python -m unittest discover -s back/tests -p 'test_onprem_readiness.py' -v`.
- 프런트 HTTP 대역: `node scripts/ci/test_platform_front.mjs`, 실제 API 설정의 Vite에서 `node scripts/ci/test_onprem_front.mjs --url http://127.0.0.1:15173`.
- 실제 배포: [실행 안내](../../docs/ONPREM.md) 후 `node scripts/ci/test_onprem_deploy.mjs --url http://127.0.0.1:15173 --zip /절대경로/sample.zip --output /절대경로/result.json`. 최종 appUrl에 `node scripts/ci/test_sample_front.mjs --url <appUrl> --mode real`로 API/DB 사용자 흐름을 별도 확인한다. 테스트 자원은 실행자 소유 범위에서 정리한다.

HTTP 대역/가짜 Docker 통과는 실제 배포 증명이 아니다. 자동 CI는 유료 호출이나 실제 앱 배포 없이 회귀만 검사한다.
