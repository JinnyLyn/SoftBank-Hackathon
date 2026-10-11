# on-prem 서비스 회귀 경계

- AWS `/`의 화면·기본 provider·기존 API 동작은 유지한다. `/onprem`은 AWS 연결/도메인 요청 없이 로컬 계획만 선택한다.
- 다른 프로젝트·다른 plan·fingerprint·target과 승인 전후 변경을 거부한다. 같은 프로젝트의 역순 추천 응답, 초기화 후 늦은 응답, AWS 공유 cache 변경도 기존 검토를 바꾸지 못한다.
- 실행기 종료·프로세스 종료·API 오류를 삼킨 0건 반환은 준비 성공이 아니다. 빈 API 목록의 실제 성공은 성공 관측이다. busy 기한은 API 성공/heartbeat로 늘어나지 않는다.
- 없는 설정/파일, 잘못된 JSON/schema/run-id, 미래/만료 시각, DB 실패는 non-ready다. 준비 실패 시 입력/검토는 유지하고 새 분석·승인만 차단한다. 이미 등록된 작업 조회는 지속한다.
- 고정 Docker endpoint, dotenv opt-out, mock/외부 API 환경 오염 차단, 포트 충돌, 중복 실행, 일부 시작 실패와 정확한 child process group 정리를 검사한다.
- 단위 검사: `uv run --no-project --python 3.12 python -m unittest discover -s scripts -p 'test_onprem_*.py' -v`, `PYTHONPATH=back uv run --no-project --python 3.12 python -m unittest discover -s back/tests -p 'test_onprem_readiness.py' -v`.
- 프런트 HTTP 대역: `node scripts/ci/test_platform_front.mjs`, 실제 API 설정의 Vite에서 `node scripts/ci/test_onprem_front.mjs --url http://127.0.0.1:15173`.
- 실제 배포: [실행 안내](../../docs/ONPREM.md) 후 `node scripts/ci/test_onprem_deploy.mjs --url http://127.0.0.1:15173 --zip /절대경로/sample.zip --output /절대경로/result.json`. 최종 appUrl에 `node scripts/ci/test_sample_front.mjs --url <appUrl> --mode real`로 API/DB 사용자 흐름을 별도 확인한다. 테스트 자원은 실행자 소유 범위에서 정리한다.

HTTP 대역/가짜 Docker 통과는 실제 배포 증명이 아니다. 자동 CI는 유료 호출이나 실제 앱 배포 없이 회귀만 검사한다.
