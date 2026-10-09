# 앱 CI

목표는 PR에서 깨지는 동작을 찾고, main에 합쳐진 커밋도 다시 검사하는 것이다. 현재 코드와 열린 PR의 구성요소에 맞춰 검사하며, 전체 플랫폼 통합 미완료 항목은 명시한다(2026-10-09 사용자 범위 확인).

## 실행과 결과

`.github/workflows/app-ci.yml`의 `Application CI`가 다음 경우 실행된다.

- main 대상 PR 생성·갱신: GitHub의 기본 checkout으로 PR과 main의 가상 병합 결과 검사.
- main push: PR이 실제로 합쳐진 커밋을 다시 검사. 병합 방식과 무관하게 main 변경을 감지한다.
- Actions의 수동 실행: 기본 브랜치에 워크플로가 합쳐진 뒤 사용할 수 있다.

경로 필터를 두지 않아 문서 PR에서도 고정된 결과가 나온다. 오래된 PR 실행은 취소하지만 main의 실행은 각 커밋별로 유지한다. 실패 시 GitHub Actions 실행 화면과 마지막 `Available app checks`의 요약에서 구성요소별 결과를 확인한다. 알림은 각 사용자의 GitHub Actions 알림 설정을 따른다. 자동 머지·롤백·AWS 배포는 하지 않는다.

**녹색 `Available app checks`는 구현된 검사 범위의 성공이다. 전체 플랫폼 정상 판정이 아니다.** 요약에는 전체 플랫폼 통합을 `미검증`으로 표시한다. 없는 구성요소는 `미구현: 검사 안 함`으로 표시하고, 존재하는 코드의 필수 파일 누락·검사 실패·취소·예상 밖 skip은 실패로 처리한다.

## 검사 범위

| 작업 | 실행 조건 | 확인하는 동작 | 확인하지 않는 것 |
|---|---|---|---|
| `CI coverage` | 항상 | 파일 계약과 검사 판정 회귀 테스트 | 플랫폼 동작 |
| `Sample frontend (MOCK browser)` | 항상 | Chrome에서 가입, 세션, 글쓰기, HTML 문자 표시, 응원/취소, 로그아웃, 로그인 오류, 새로고침 후 유지. API 없는 서버를 real 모드가 거부하는지도 확인 | 실제 API·DB |
| `Sample app (real MySQL and browser)` | `sample-back/` 구현 존재 | 기존 Compose·Dockerfile 빌드, MySQL 8.4, DB 조회 `/health`, JSON 오류, 인증·쿠키 폐기, 글/투표 저장, 실제 브라우저에서 MOCK 전환 없음, DB 중지 후 `/health` JSON 500 | AWS/RDS, 부하, DB 재시작 후 복구·마이그레이션 |
| `Platform frontend (typecheck and build)` | `front/` 구현 존재 | `npm ci`, TypeScript/Vite의 MOCK·실제 API 설정 빌드 | 실제 SSO/API/분석/승인/배포 흐름 |
| `Infrastructure (static validation)` | `infra/` 구현 존재 | Terraform 1.16.5의 fmt, backend 비활성 init, validate, deploy.sh 셸 문법 | AWS plan/apply/destroy, 헬스·롤백·비용 |

현재 main에는 샘플 프런트만 있다. [PR #4](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/4), [#6](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/6), [#7](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/7)의 파일 계약을 읽어 조건부 검사를 준비했다. 각 PR이 main을 동기화하면 PR 검사에서, 합쳐지면 main push에서 활성화된다. 열린 PR 코드를 CI가 별도로 가져오거나 자동으로 합치지 않는다.

`infra/scripts/test_infra.py --skip-aws` 전체를 CI에 연결하지 않았다. 확인한 버전은 foundation 실조회만 건너뛰고 다른 plan은 일반 AWS provider를 사용한다. 인증 없는 환경의 전체 회귀 검사라는 근거가 부족하다. 격리된 provider mock을 갖춘 뒤 별도 검사로 추가한다.

## 로컬 재현

Python 3.9 이상, Node 22.12 이상, Chrome/Chromium이 필요하다. 브라우저는 `CHROME_BIN`으로 지정할 수 있고 기본값은 PATH의 `google-chrome`, `chromium`, `chromium-browser` 순서다. npm/pip 테스트 패키지를 추가하지 않는다.

```bash
python3 -m unittest discover -s scripts/ci -p 'test_components.py' -v
python3 scripts/ci/components.py inspect
node --check sample-front/app.js
node --check sample-front/mock-api.js
python3 -m http.server 8080 --bind 127.0.0.1 --directory sample-front
```

다른 터미널에서 브라우저 검사를 실행하고, 끝나면 위 정적 서버를 종료한다.

```bash
node scripts/ci/test_sample_front.mjs --url http://127.0.0.1:8080/ --mode mock
```

샘플 백엔드가 합쳐졌고 Docker Compose v2를 쓸 수 있으면 아래 명령으로 실제 통합 검사를 실행한다. 로컬 8000·3307 포트가 비어 있어야 한다. 기존 Compose 파일의 MySQL 8.4와 Dockerfile을 그대로 사용한다. 임의 이름의 CI 전용 프로젝트·테스트 계정·볼륨을 만들고 종료 시 그 프로젝트만 `down --volumes`로 정리한다. 실서비스를 검사 대상으로 지정하지 않는다.

```bash
bash scripts/ci/run_sample_stack.sh
```

프런트와 인프라가 합쳐진 뒤에는 워크플로와 같은 명령을 사용한다.

```bash
npm ci --prefix front
VITE_USE_MOCK=true npm run build --prefix front
VITE_USE_MOCK=false npm run build --prefix front
terraform -chdir=infra fmt -check -recursive
for root in infra/bootstrap infra/foundation infra/deployments/_template; do
  terraform -chdir="$root" init -backend=false -input=false
  terraform -chdir="$root" validate
done
bash -n infra/scripts/deploy.sh
```

## 검사 확장과 운영

- 코드가 있는데 검사 계약 파일이 없으면 inventory가 실패한다. PR의 base SHA 또는 main push 직전 SHA에 있던 구성요소가 통째로 사라져도 실패한다(비교 커밋이 없는 수동 실행은 현재 파일 계약만 검사). 디렉터리 이동·manifest·실행 계약 변경 시 `scripts/ci/components.py`, 워크플로, 해당 README와 이 문서를 함께 바꾼다.
- `back/`에 구현이 생기면 현재 CI는 의도적으로 실패한다. 실행 manifest·임시 DB·실제 API와 프런트 통합 검사를 연결한 뒤 이 가드를 대체한다. 단순 존재 여부만으로 성공시키지 않는다.
- 전체 플랫폼 검증에는 소스 입력→분석→가격 계산→선택→검토·승인→실행→상태·실패 처리의 계약이 필요하다. LLM·AWS 모의 검사는 그 사실을 표시하고 실제 외부 연동 시험과 구분한다. 회의 변경 근거는 `MEETING_UPDATES.md`를 따른다.
- 새 검사를 필수 check로 등록할 때는 최초 원격 성공을 확인한 뒤 고정 이름 `Available app checks`를 사용한다. 조건부 개별 job을 필수로 등록하면 미통합 컴포넌트 때문에 정상 PR도 막힐 수 있다. 등록 여부는 `GITHUB_SETUP.md`에 기록한다.
- Actions 권한은 `contents: read`, checkout은 자격 증명 저장 해제, 외부 Action은 commit SHA 고정이다. AWS·LLM 비밀키, production 환경, Terraform plan/state를 사용하지 않는다. CI 임시 DB의 예시 비밀번호를 운영 자격 증명으로 재사용하지 않는다.
- 작업마다 timeout을 두고 브라우저 프로세스·프로필과 Compose 프로젝트를 정리한다. runner 강제 종료 시 cleanup 완료를 보장하지 않으므로 GitHub의 일회용 runner에서 실행한다. raw 쿠키·응답·앱 로그·DB·Terraform state를 artifact로 업로드하지 않는다.

## 공식 근거

- [GitHub PR·push 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [needs 결과와 조건부 작업](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context)
- [Actions 권한과 SHA 고정](https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions)
- [ubuntu-24.04 runner 도구](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
- [Node 내장 WebSocket](https://nodejs.org/download/release/v22.15.0/docs/api/globals.html#class-websocket), [Chrome remote debugging의 별도 프로필 요구](https://developer.chrome.com/blog/remote-debugging-port)
