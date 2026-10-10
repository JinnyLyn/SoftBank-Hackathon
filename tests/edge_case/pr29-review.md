# PR #29 리뷰 회귀 사례

- Dockerfile 없는 Express 앱이 설정 파일의 포트 7311을 읽는 경우: 초안 기본값 3000이 LLM 분석 대상을 지우지 않아야 한다. 근거 검증 후 `app_config`, 초안 포트, `ENV PORT`, `EXPOSE`가 모두 7311이어야 한다.
- Python 초안의 기본 포트 8000을 LLM이 5050으로 확인한 경우: gunicorn 바인드와 `EXPOSE`도 5050으로 갱신한다. 빈 답·잘못된 근거는 원래 초안과 미해결 상태를 유지한다.
- 비밀 이름에 `true/false/null/none/yes/no/on/off/~`가 쓰인 경우: 값의 모양과 무관하게 가린다. 압축·여러 줄 JSON의 `private` 불리언은 보존하고 비밀 스칼라는 유효한 JSON 문자열로 가린다.
- Python `requirements.txt`가 중첩 `-r`, `-c`, 로컬 editable 패키지를 참조하는 경우: 첫 pip 설치 전에 해당 소스가 빌드 경로에 있어야 한다.
- LLM 응답의 `field`가 배열·객체이면 잘못된 출력으로 처리하고 캐시하지 않는다. `package.json`의 `scripts`가 객체가 아니면 초안을 만들지 못한 이유를 반환한다. 두 경우 모두 분석 작업자를 종료시키지 않는다.
- 캐시 파일의 손상·권한·쓰기 실패는 원문이나 경로 없이 경고한다. 교체에 실패하면 해당 작업이 만든 임시 응답 파일을 정리한다(`ai/tests/test_cache.py`).
- 스키마가 틀린 LLM 응답은 캐시 유무와 관계없이 포트·메모·근거를 변경하지 못한다. 캐시 버전도 변경해 기존 형식 검증 이전의 결과와 분리한다.
- 빌드 없이 `start: tsx server.ts`만 있는 npm·pnpm·yarn 앱도 개발 의존성의 실행 도구를 유지한다.
- `next.config.js/mjs/cjs/ts`가 있으면 `/shop` 등 실제 경로를 평가하기 전까지 파일 라우트만으로 `/`·`/api/health`를 확정하지 않는다. 설정 파일은 LLM 입력의 우선 선택 대상이다.

회귀 검사는 `ai/tests/test_llm.py`, `test_masking.py`, `test_dockerfile.py`에서 수행한다. 실제 LLM·AWS 배포 성공을 검증하는 테스트는 아니다.
