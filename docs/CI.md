# 앱 CI

목표는 PR에서 깨지는 동작을 찾고, main에 합쳐진 커밋도 다시 검사하는 것이다. 2026-10-10 초기 단계에 맞춘 CI 조정 요청을 반영해 각 파트가 독립적으로 실행·검증되는지를 먼저 확인한다. 아직 구현되지 않은 다른 파트나 전체 제품 흐름을 개별 파트 병합의 전제조건으로 삼지 않는다.

현재 제품 방향은 [PRODUCT_DIRECTION.md](PRODUCT_DIRECTION.md)의 `2026-10-10-managed-domains-v1`이다. 도메인 화면·MOCK·타입 검사 성공은 실제 도메인 구매·DNS·HTTPS 검증을 뜻하지 않는다. 기존 확보 도메인 연결(A) / 신규 구매 자동화(B) 중 이번 시연의 선택은 별도 확인하며, B 전체 완성을 모든 PR의 병합 조건으로 추가하지 않는다.

도메인 기능이 연결되면 담당 파트가 합의한 API·상태 전이·승인·재시도와 앱 삭제 시 도메인 보존을 검사한다. 구매 기능을 선택하면 중복 구매 방지도 검사한다. 실제 DNS·인증서·HTTPS 결과, 수동 사전 준비, 코드 SHA·시험 시각은 별도 승인된 시험에서 증빙한다. 일반 PR CI에서 유료 등록이나 실제 AWS 변경을 자동 실행하지 않는다. 이번 문서 갱신은 Actions YAML이나 새 검사 구현을 추가하지 않는다.

## 초기 단계의 원칙과 담당

모든 팀에 적용되는 단일 CI 구성이나 고정 테스트 비율은 없다. [DORA의 CI 지침](https://dora.dev/capabilities/continuous-integration/)은 작은 변경을 자주 통합하고, 테스트가 없다면 핵심 기능의 소수 테스트부터 시작하도록 권고한다. [Google의 테스트 구성 설명](https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html)도 많은 빠른 테스트·필요한 통합 테스트·소수 E2E를 권고하며 비율은 팀에 따라 달라진다고 설명한다. 다음 단계는 이 원칙을 현재 프로젝트에 적용한 선택이며 강제 산업 표준은 아니다.

| 단계 | 검사 범위 | 현재 적용 |
|---|---|---|
| 파트 독립 검증 | manifest 기반 설치, 문법·타입·빌드, 작은 동작 검사 | 프런트 빌드, 플랫폼 백엔드 기동·HTTP smoke, 인프라 정적 검사 |
| 연결된 경계 검증 | 실제 임시 DB/API, 합의한 요청·응답 계약 | 샘플 앱 DB·브라우저와 플랫폼 MySQL·migration·마스킹·롤백 승인 검사를 실행. 플랫폼 프런트/API는 후속 통합 작업 |
| 제품 흐름 검증 | 소스 입력→분석→승인→배포 등 소수 핵심 E2E | 연결된 흐름부터 추가. 실제 AWS·유료 LLM 실행은 일반 PR 검사와 분리 |

- **기능 담당자:** 변경한 동작의 테스트와 실행 방법을 코드와 함께 유지한다. CI 실패를 자신의 환경에서 재현할 수 있게 한다.
- **CI 담당자:** 공통 runner·의존성 설치·job 연결·최종 결과 판정을 관리한다. 각 기능의 테스트를 혼자 대신 작성하는 역할은 아니다.
- **연결하는 파트의 담당자들:** API·스키마 계약과 통합 검사를 함께 갱신한다. 연결이 가능해진 범위를 계속 미검증으로 방치하지 않는다.
- 골격뿐인 파트는 검사 제외 사유를 표시한다. 구현은 있는데 실행 계약이 없거나 실제 검사가 실패하면 차단한다. 제품 전체가 미완성이라는 이유만으로 실패시키지는 않는다.
- 외부 LLM·AWS 의존성은 일반 테스트에서 대역으로 분리할 수 있다. MOCK 통과를 실제 연동 성공으로 보고하지 않는다. 기존에 실제 동작을 확인하던 검사를 MOCK으로 낮추지 않는다.

테스트 소유권과 외부 서비스 대역의 근거는 [DORA 테스트 자동화](https://dora.dev/capabilities/test-automation/)와 [FastAPI 의존성 대체 문서](https://fastapi.tiangolo.com/advanced/testing-dependencies/)다. 확인일은 2026-10-10이며 Google 글은 최신 API가 아닌 테스트 구성 원칙의 참고 자료다.

## 실행과 결과

### PR 보고 검사

`Team Git Guard`의 `Git guard (ubuntu-latest)`·`Git guard (windows-latest)`에 PR 보고 검사를 포함한다. 2026-10-10 재조회한 서버 필수 check는 `Available app checks` 하나이며, Git guard 두 작업은 서버 필수 목록에 없다([현재 설정](GITHUB_SETUP.md)). 머지 담당자는 두 작업의 최신 실행 성공을 직접 확인하고 실패·진행 중·미실행이면 머지하지 않는다. `Available app checks` 성공이나 GitHub의 머지 가능 상태가 보고 검사 성공을 대신하지 않는다. 이 워크플로가 반영된 main을 기준으로 사용하며, 기존 PR도 최신 main과 동기화하고 보고 항목을 채운다.

- main 대상 PR의 생성·본문/제목 수정·추가 push·재오픈에 실행한다(`opened`, `edited`, `synchronize`, `reopened`). 본문 수정은 이 워크플로를 재실행하며 앱 빌드 전체를 재실행하지 않는다. 같은 PR의 이전 실행은 취소하고 최신 실행을 판정한다.
- 템플릿의 필수 제목이 하나 있고 `해당 없음`·`해당 있음` 중 하나만 체크됐는지 확인한다. `해당 있음`은 항목마다 관련 기준, 실제 변경, 이유, 영향, 문서 처리, 미결 사항을 작성해야 한다. 빈칸·TODO·TBD·이유 없는 `미정`은 실패한다. `TODO: 추후 작성`, `TBD - 담당자 확인`처럼 뒤에 설명을 붙인 미작성 표시도 상세 항목과 문서 처리 사유에서 거부한다. `해당 없음`이면 상세 항목은 남기거나 삭제할 수 있다.
- 제목·항목 이름과 블록 순서는 템플릿을 유지한다. 상세 내용은 여러 줄로 쓸 수 있지만 주석·코드 블록만으로 대신하지 않는다. 설명의 사실성·충분성이나 숨겨진 우회는 자동 판정하지 않는다.
- 제품 기준 ID·확인한 main SHA·도메인 A/B 범위와 증빙은 작성자와 리뷰 담당이 확인한다. 현재 검사기는 이 값의 최신성이나 실제 구현 여부를 자동 검증하지 않는다.
- 문서 처리 사유는 `변경 불필요(기존 기준 유지)`처럼 괄호로 붙여 써도 된다. ASCII·전각 괄호와 공백 유무를 허용하며, 괄호 안 빈칸·TODO·TBD는 계속 거부한다. Git guard의 보고 오류는 앱·롤백 검사 실패와 구분한다.
- 실패 메시지에 나온 항목을 PR 본문에서 보완하면 새 실행으로 재검사한다. 이미 완료된 실행의 재실행은 당시 이벤트 본문을 사용하므로, 본문 변경 뒤 생성된 최신 실행을 확인한다.
- 이벤트 파일 `GITHUB_EVENT_PATH`를 JSON 데이터로 읽는다. PR 본문을 셸 코드에 삽입하거나 로그에 출력하지 않고 추가 토큰·외부 호출을 사용하지 않는다.
- main push와 수동 실행은 보고 검사 자체의 회귀 테스트만 실행하며 PR 본문을 요구하지 않는다.

로컬에서 회귀 검사와 제출할 본문을 확인한다.

```bash
python3 -m unittest discover -s scripts/ci -p 'test_pr_report.py' -v
python3 scripts/ci/check_pr_report.py --body-file /path/to/pr-body.md
```

### 앱 검사

`.github/workflows/app-ci.yml`의 `Application CI`가 다음 경우 실행된다.

- main 대상 PR 생성·갱신: GitHub의 기본 checkout으로 PR과 main의 가상 병합 결과 검사.
- main push: PR이 실제로 합쳐진 커밋을 다시 검사. 병합 방식과 무관하게 main 변경을 감지한다.
- Actions의 수동 실행: 기본 브랜치에 워크플로가 합쳐진 뒤 사용할 수 있다.

경로 필터를 두지 않아 문서 PR에서도 고정된 결과가 나온다. 오래된 PR 실행은 취소하지만 main의 실행은 각 커밋별로 유지한다. 실패 시 GitHub Actions 실행 화면과 마지막 `Available app checks`의 요약에서 구성요소별 결과를 확인한다. 알림은 각 사용자의 GitHub Actions 알림 설정을 따른다. 자동 머지·롤백·AWS 배포는 하지 않는다.

**녹색 `Available app checks`는 구현된 검사 범위의 성공이다. 전체 플랫폼 정상 판정이 아니다.** 요약에는 전체 플랫폼 통합을 `미검증`으로 표시한다. 없는 구성요소는 `미구현: 검사 안 함`으로 표시하고, 존재하는 코드의 필수 파일 누락·검사 실패·취소·예상 밖 skip은 실패로 처리한다.

## 검사 범위

| 작업 | 실행 조건 | 확인하는 동작 | 확인하지 않는 것 |
|---|---|---|---|
| `CI coverage` | 항상 | 파일 계약·검사 판정·실패 진단의 회귀 테스트 | 플랫폼 동작 |
| `Sample frontend (MOCK browser)` | 항상 | Chrome에서 가입, 세션, 글쓰기, HTML 문자 표시, 응원/취소, 로그아웃, 로그인 오류, 새로고침 후 유지. API 없는 서버를 real 모드가 거부하는지도 확인 | 실제 API·DB |
| `Sample app (real MySQL and browser)` | `sample-back/` 구현 존재 | 기존 Compose·Dockerfile 빌드, MySQL 8.4, DB 조회 `/health`, JSON 오류, 인증·쿠키 폐기, 글/투표 저장, 실제 브라우저에서 MOCK 전환 없음, DB 중지 후 `/health` JSON 500 | AWS/RDS, 부하, DB 재시작 후 복구·마이그레이션 |
| `Platform frontend (typecheck and build)` | `front/` 구현 존재 | `npm ci`, TypeScript/Vite의 MOCK·실제 API 설정 빌드 | 실제 API/분석/승인/배포 흐름 |
| `Platform backend (API smoke and MySQL contracts)` | `back/` 구현 존재 | `.python-version`·requirements 기반 설치, 문법·DB 없는 smoke, 마스킹 회귀, 백엔드 Docker 빌드, 격리 MySQL 8.4 migration·재실행, 첫 실패 롤백 차단, 새 plan/diff 승인·claim, 오래된 승인·동시 등록 차단, 실제 API의 비밀 저장 거부·로그 마스킹 | 프런트 연동, LLM·AWS Terraform/ECS 실행, 업로드 앱 이미지 빌드 |
| `Infrastructure (static validation)` | `infra/` 구현 존재 | Terraform 1.16.5의 fmt, backend 비활성 init, validate, deploy.sh 셸 문법 | AWS plan/apply/destroy, 헬스·롤백·비용 |

10/10 확인한 main `432a52e`에는 샘플 프런트와 [PR #4](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/4)의 샘플 백엔드가 있다. 플랫폼 백엔드는 [PR #10](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/10) `147ad5e`, 인프라·플랫폼 프런트는 [#6](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/6)·[#7](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/7)의 파일 계약에 맞춘 조건부 검사다. 각 PR이 변경된 CI를 반영하면 해당 구현이 있는 검사만 활성화된다. 열린 PR 코드를 CI가 별도로 가져오거나 자동으로 합치지 않는다.

PR #10에서 실패했던 `back/` 존재 자체의 차단 가드는 제거하고 위의 기본 검사를 연결했다. 아직 없는 프런트·LLM·배포 worker 완성을 요구하지 않는다. 단, DB 없는 smoke는 DB 준비나 실제 배포가 된다는 증거가 아니며 결과 요약에도 이 제한을 표시한다.

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

샘플 백엔드가 합쳐졌고 Docker Compose v2를 쓸 수 있으면 아래 명령으로 실제 통합 검사를 실행한다. 로컬 8000·3307 포트가 비어 있어야 한다. 기존 Compose 파일의 MySQL 8.4와 Dockerfile을 그대로 사용하며 DB 준비→`init-db` 정상 종료→앱 시작 순서를 따른다. 임의 이름의 CI 전용 프로젝트·테스트 계정·볼륨을 만들고 종료 시 그 프로젝트만 `down --volumes`로 정리한다. 실서비스를 검사 대상으로 지정하지 않는다.

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

플랫폼 백엔드가 있는 브랜치에서는 `back/.python-version`의 Python과 별도 가상환경을 사용한다. 서비스 키·DB를 준비할 필요 없이 다음을 실행할 수 있다.

```bash
python -m pip install -r back/requirements.txt
python -m pip check
python -m compileall -q back/app
python scripts/ci/test_platform_back.py
python -m unittest discover -s scripts/ci -p 'test_platform_back_secrets.py' -v
```

smoke 스크립트는 loopback의 임시 포트에서 실제 Uvicorn을 실행한다. 서비스 자격 증명·프록시·DB 설정을 상속하지 않고 임시 업로드 경로를 사용한다. 기동·요청에 제한 시간을 두고 정상/실패 시 해당 프로세스와 임시 파일을 정리한다. 없는 DB 설정에 대해 503을 내는지 검사하며, 실제 DB 연결 성공을 흉내 내지 않는다.

실패 시 정리 전에 로그 끝의 최대 16 KiB에서 traceback 파일명·줄 번호·예외 종류 또는 Uvicorn 오류 여부를 추출해 진단 항목 최대 12줄을 남긴다. 소스 코드·예외 메시지·환경값·원문 로그는 출력하지 않는다. 표시된 위치의 코드를 확인하고 동일한 smoke 명령으로 재현한다.

플랫폼 DB 통합 검사는 Linux/WSL의 Bash·GNU `timeout`·Python 3·Docker Compose v2로 저장소 루트에서 실행한다. API Python은 `back/Dockerfile`의 3.13.16, DB는 `back/compose.yaml`과 같은 MySQL 8.4.11을 사용한다.

```bash
bash scripts/ci/run_platform_stack.sh
```

runner는 CI 전용 Compose 구성으로 임의 프로젝트·네트워크·볼륨을 만들고, 호스트 포트와 `.env`·AWS/LLM/기존 worker 키를 전달하지 않는다. API 기동 후 모든 migration을 두 차례 실행해 적용 이력을 확인하고 `test_platform_back_rollbacks.py`, `test_platform_back_masking.py`를 컨테이너 안에서 실행한다. 성공·실패 시 생성한 프로젝트만 `down --volumes`로 정리한다. 각 단계에 timeout을 두며 원문 서비스 로그는 출력하지 않는다.

이 검사는 실제 DB·HTTP를 사용한다. 업로드하는 plan 바이트와 worker 상태 보고는 합성 테스트 데이터이며 실제 Terraform plan/apply·AWS rollback 성공을 의미하지 않는다. [Issue #16](https://github.com/JinnyLyn/SoftBank-Hackathon/issues/16) 이전에는 롤백 검사 파일만 있었고 workflow에서 호출하지 않았으며, 이 runner 연결부터 DB 검증 범위가 추가된다.

## 검사 확장과 운영

날짜를 정해 한꺼번에 강화하기보다 아래 작업이 생기는 시점에 해당 담당자가 검사를 추가한다.

| 시점 | 담당과 실행 방식 |
|---|---|
| 이번 CI 변경이 main에 반영된 직후 | 열린 PR에 최신 main을 반영하고 해당 파트의 조건부 job이 실제로 실행·성공하는지 확인한다. 이 CI PR의 backend skip을 PR #10 검증으로 대신하지 않는다. |
| 플랫폼 백엔드의 다음 검증 작업 | 현재 migration·마스킹·롤백 계약 검사에 변경 동작의 회귀를 추가한다. 전체 CRUD·연결 완료·재기동 후 영속성 등의 미검증 경계는 백엔드 담당자가 확장하고 CI 담당자가 연결한다. |
| 프런트와 API를 연결하는 PR | 양쪽 담당자가 실제 API 설정으로 정상·오류 응답을 검사한다. MOCK 자동 전환으로 실패를 숨기지 않는다. |
| LLM·배포 worker를 연결하는 PR | 연결 담당자가 외부 호출 대역으로 잘못된 출력·timeout·승인 전 실행 차단·실패 상태를 검사한다. 일반 PR에서는 실제 키·유료 호출을 요구하지 않는다. |
| 데모 리허설 전 | 발표자 환경에서 실제 입력→분석→승인→선택한 대상 배포→상태 확인을 실행한다. 실제 AWS 실행은 담당자의 명시적인 실행 요청 범위로 제한하고 실패·정리 결과까지 기록한다. |

기능 동작을 바꾸는 PR은 해당 테스트를 함께 갱신한다. 재현된 버그는 수정과 함께 회귀 테스트를 추가한다. 후속 통합 검사가 남아 있다는 이유만으로 현재의 독립 CI 개선을 보류하지 않는다.

- 코드가 있는데 검사 계약 파일이 없으면 inventory가 실패한다. PR의 base SHA 또는 main push 직전 SHA에 있던 구성요소가 통째로 사라져도 실패한다(비교 커밋이 없는 수동 실행은 현재 파일 계약만 검사). 디렉터리 이동·manifest·실행 계약 변경 시 `scripts/ci/components.py`, 워크플로, 해당 README와 이 문서를 함께 바꾼다.
- `back/`는 `.python-version`, `requirements.txt`, `app/main.py`와 Dockerfile·migration 실행 모듈·필수 SQL이 있어야 검사를 실행한다. 정확한 파일 목록은 `scripts/ci/components.py`의 계약을 따른다. 구현 존재만으로 통과시키지 않고 실제 기동·HTTP·DB 검사 결과를 판정한다.
- 플랫폼 프런트 API 계약이 연결되면 해당 경계 검사를 추가한다. 현재 DB 계약 검사의 성공을 전체 CRUD·프런트·worker 통합 완료로 처리하지 않는다.
- 전체 플랫폼 검증에는 소스 입력→분석→가격 계산→선택→검토·승인→실행→상태·실패 처리의 계약이 필요하다. LLM·AWS 모의 검사는 그 사실을 표시하고 실제 외부 연동 시험과 구분한다. 회의 변경 근거는 `MEETING_UPDATES.md`를 따른다.
- 새 검사를 필수 check로 등록할 때는 최초 원격 성공을 확인한 뒤 고정 이름 `Available app checks`를 사용한다. 조건부 개별 job을 필수로 등록하면 미통합 컴포넌트 때문에 정상 PR도 막힐 수 있다. 등록 여부는 `GITHUB_SETUP.md`에 기록한다.
- workflow 전체의 경로 필터는 필수 검사를 Pending으로 남길 수 있으므로 현재는 사용하지 않는다. job의 skip은 그 자체로 실패가 아니므로, 항상 실행되는 최종 job이 탐지 결과와 `needs`를 대조해 실행 대상의 실패·취소·예상 밖 skip을 차단한다. [GitHub 필수 검사 문제 해결](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- Actions 권한은 `contents: read`, checkout은 자격 증명 저장 해제, 외부 Action은 commit SHA 고정이다. AWS·LLM 비밀키, production 환경, Terraform plan/state를 사용하지 않는다. CI 임시 DB의 예시 비밀번호를 운영 자격 증명으로 재사용하지 않는다.
- 작업마다 timeout을 두고 브라우저 프로세스·프로필과 Compose 프로젝트를 정리한다. runner 강제 종료 시 cleanup 완료를 보장하지 않으므로 GitHub의 일회용 runner에서 실행한다. raw 쿠키·응답·앱 로그·DB·Terraform state를 artifact로 업로드하지 않는다.

## 공식 근거

- [GitHub PR·push 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [needs 결과와 조건부 작업](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context)
- [Actions 권한과 SHA 고정](https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions)
- [ubuntu-24.04 runner 도구](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
- [Node 내장 WebSocket](https://nodejs.org/download/release/v22.15.0/docs/api/globals.html#class-websocket), [Chrome remote debugging의 별도 프로필 요구](https://developer.chrome.com/blog/remote-debugging-port)
