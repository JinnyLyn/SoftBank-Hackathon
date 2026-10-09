# Paved Clouds — 팀 작업 방식

이 문서는 Codex·Claude Code·터미널·IDE에서 같은 작업 절차를 사용하기 위한 기준이다.
프로젝트 내용은 `AGENTS.md`, 남은 결정은 `docs/OPEN_QUESTIONS.md`를 읽는다.

## 1. 브랜치는 사람마다 하나

| GitHub 계정 | 계속 사용할 브랜치 |
|---|---|
| JinnyLyn | `JinnyLyn` |
| JinVibe | `JinVibe` |
| Ophelia0419 | `Ophelia0419` |
| totorosi | `totorosi` |
| wisetg | `wisetg` |

- 자기 개인 브랜치를 계속 사용한다. 기능별 브랜치를 추가로 만들거나 다른 팀원 브랜치에 직접 커밋·푸시하지 않는다.
- `main`은 통합 기준이다. 직접 커밋·푸시하지 않고 PR로 합친다.
- PR 기본 리뷰·머지 담당은 `JinnyLyn`이다. 다른 팀원 PR은 JinnyLyn의 승인을 받는다.
- **JinnyLyn 본인 PR은 팀원 승인 없이 직접 머지할 수 있다.** PR을 만들고 검증 결과·diff를 확인한 뒤 PR 화면의 우회를 사용한다. main 직접 커밋·푸시는 여전히 금지다.
- 다른 팀원 PR은 작성자 외 승인, 필수 검사 통과, 리뷰 대화 해결 후 합친다. AI 리뷰 결과는 필요한 사람 승인을 대체하지 않는다. 본인 PR도 필수 검사를 생략하지 않는다.
- 개인 브랜치를 재사용하므로 **Create a merge commit**으로 합친다. squash/rebase merge와 자동 브랜치 삭제는 사용하지 않는다.
- 같은 개인 브랜치에서 여러 에이전트가 동시에 Git 작업을 하지 않는다. 리뷰·읽기는 가능하지만 쓰기 작업은 한 세션씩 진행한다.
- 에이전트가 자동으로 만드는 기능 브랜치·worktree를 사용하지 않는다. 필요한 예외는 리뷰 담당과 먼저 합의한다.

## 2. 처음 한 번 설정

필요 도구는 Git 2.40 이상과 Python 3.9 이상이다. Windows는 Git for Windows를 사용한다. 훅은 Git이 실행하므로 에이전트별 플러그인은 필요 없다.

저장소를 clone한 뒤, 이 파일과 `.githooks/`, `scripts/setup_git.py`, `.github/team-branches.json`이 있는 버전에서 실행한다. 아래 `내아이디`는 위 표의 정확한 계정명으로 바꾼다.

```text
git fetch origin
python scripts/setup_git.py 내아이디
```

Windows에서 Python 명령이 `py`이면 `py -3`으로, macOS/Linux에서 `python3`이면 그 명령으로 바꾼다.
처음 설정할 때 파일이 아직 main에 없다면 먼저 해당 설정 PR을 합친 뒤 main을 받아 설정한다.

- 스크립트는 Git 설정과 훅만 설치한다. 커밋·푸시·기존 변경 삭제·자동 브랜치 전환은 하지 않는다.
- 기존 사용자 훅이 있으면 덮어쓰지 않고 알려준다. 리뷰 담당과 함께 통합한다.
- GitHub 사용자명은 본인의 계정으로 지정한다. 이 설정은 계정 인증을 대신하지 않는다.
- PC나 clone이 바뀌면 다시 설치한다. 훅 원본이 업데이트되면 같은 명령으로 다시 설치한다.
- 작업 시작 전에 `python scripts/setup_git.py --check`로 확인한다. 실패하면 쓰기 작업 전에 설치 상태를 복구한다.

훅을 끄거나 `--no-verify`, `-c core.hooksPath=...` 등으로 우회하지 않는다. 로컬 훅은 실수 방지 장치이고 최종 원격 차단은 GitHub ruleset이 담당한다.

## 3. 작업 시작·재개

1. `git status`로 미커밋 변경과 현재 브랜치를 확인한다. 다른 사람 변경을 버리거나 임의로 stash하지 않는다.
2. 배정 브랜치가 로컬에 있으면 `git switch 내아이디`로 전환한다. 처음 한 번만 `git switch --track origin/내아이디`로 같은 이름의 로컬 브랜치를 만든다.
3. 작업 트리가 깨끗할 때 아래 순서로 동기화한다.

```text
git fetch origin
git merge --ff-only origin/내아이디
git merge origin/main
```

- 개인 원격 브랜치와 로컬이 갈라져 첫 merge가 실패하면 기록을 비교한다. 강제 푸시·reset으로 덮어쓰지 않는다.
- main 동기화에서 충돌하면 양쪽 변경 의도를 확인해 해결한다. API·설계 충돌은 해당 담당자와 확인한다.
- 미커밋 변경이 있으면 먼저 확인 가능한 단위로 저장하거나 별도 합의한 방식으로 보존한다. 스크립트가 자동으로 commit/stash하지 않는다.
- 작업이 길어지거나 다른 파트가 합쳐졌을 때, PR 제출 전에도 같은 절차를 사용한다.

## 4. 작업 완료 → 리뷰 → 다음 작업

1. 검증 가능한 한 작업 단위를 완성한다. 개인 브랜치 하나라고 모든 작업을 한 PR에 모으지 않는다.
2. 변경 영역에 필요한 검사와 자체 리뷰를 하고, 최신 main을 반영한다.
3. 자기 브랜치에 푸시하고 **개인 브랜치 → main** PR을 연다. `.github/PULL_REQUEST_TEMPLATE.md`를 채운다.
4. PR이 열려 있는 동안 같은 브랜치에 푸시하면 그 PR에 계속 추가된다. 관련 없는 다음 작업은 머지 이후 시작한다. 대기 중에는 읽기·조사·설계 등 독립 작업을 할 수 있다.
5. JinnyLyn이 리뷰·머지한다. JinnyLyn 작성 PR은 팀원 승인 없이 검증·diff 확인 후 PR 화면에서 머지한다.
6. merge commit으로 합친 뒤 개인 브랜치를 삭제하지 않는다. 같은 브랜치에서 `git fetch origin` → `git merge origin/main`을 수행하고 다음 작업을 시작한다.

```text
git push -u origin 내아이디
```

PR 승인·머지는 제품의 실제 AWS 배포 승인이 아니다. 실제 배포와 foundation 변경·삭제는 별도로 승인된 절차를 따른다.

## 5. 검증 명령

현재 main에서 확인한 실행·검증 범위만 적었다. 백엔드·인프라가 합쳐지면 각 담당자가 실제 명령과 결과를 이 표 또는 해당 README에 추가한다.

| 변경 영역 | 현재 실행 방법 | 확인할 것 |
|---|---|---|
| 샘플 프런트 | `python -m http.server 8080 --directory sample-front` | 로그인·가입·글쓰기·응원, MOCK 표시 |
| Git 훅 | `python scripts/test_git_hooks.py` | 허용/차단 경로 회귀 검사. 외부 GitHub에는 쓰지 않음 |
| 설치 상태 | `python scripts/setup_git.py --check` | 개인 브랜치·훅 설치 버전 |
| 플랫폼 백엔드·프런트 | 실제 진입점·의존성 확정 후 기재 | 아직 없는 명령을 지어내지 않음 |
| Terraform | 모듈·root가 합쳐지면 해당 경로에서 fmt·init·validate | 실제 AWS 적용은 담당자 실행 요청과 별도 증빙 |

- CI에 없는 검사를 통과했다고 쓰지 않는다. 미실행 항목은 이유를 적는다.
- API·DB 연결은 mock 자동 대체가 발생하지 않았는지 확인한다.
- 공유 계약을 바꾸면 호출하는 파트도 확인하고 문서를 같은 PR에서 갱신한다.
- 비밀값·plan·state·업로드 원본을 PR에 첨부하지 않는다. 재현 절차와 마스킹된 결과를 남긴다.

## 6. 브랜치 보호가 맡는 일

| 위치 | 보호 범위 | 한계 |
|---|---|---|
| 로컬 Git 훅 | 개인 브랜치 외 커밋, main 푸시, 추가 로컬 브랜치 생성·변경 차단 | 설치가 필요하며 사용자가 Git 설정을 바꾸면 우회 가능 |
| GitHub main 규칙 | PR·승인·리뷰 대화, 삭제·강제 푸시 차단 | 로컬 PC의 커밋은 통제하지 않음 |
| GitHub 브랜치 규칙 | 허용 목록 밖 원격 브랜치 생성 차단, 개인 브랜치 삭제·강제 푸시 차단 | 계정별로 자기 이름의 브랜치만 수정하게 하는 권한 체계와는 다름 |

일반 collaborator가 다른 개인 브랜치에 원격 푸시하는 행위까지 계정 기준으로 제한하려면 별도의 권한 설계가 필요하다. 이 개인 소유 저장소에서는 로컬 훅·팀 규칙으로 이를 막고, 서버의 강제 범위는 위 표대로 설명한다.

JinnyLyn의 리뷰 예외는 GitHub의 `For pull requests only` 우회 권한으로 구현한다. 이 권한은 작성자별 조건이 아니라 계정에 부여되므로 기술적으로 다른 사람의 PR도 우회할 수 있다. 팀 운영에서는 본인 PR에만 사용한다. 지정 리뷰 담당은 이 PR의 CODEOWNERS가 main에 합쳐진 뒤 적용된다. 서버 설정·필수 검사 상태는 `docs/GITHUB_SETUP.md`를 확인한다.

## 7. 기존 브랜치에서 옮기는 경우

`totorosi` 등 기존 개인 브랜치는 그대로 유지한다. 10/7에 있던 `feat/front`는 10/8 재조회 시 원격 목록에서 사라졌고 JinVibe 브랜치는 갱신되어 있었다. 이 설정 작업은 기존 작업을 삭제·초기화하지 않는다. 로컬에 옛 브랜치가 남아 있으면 아래 절차로 확인한다.

- 기존 PR·미커밋 작업·main에 없는 커밋을 확인한다.
- 본인 변경을 본인 사용자명 브랜치로 안전하게 옮기고 비교·검증한 뒤 이후 작업을 이어간다.
- 기존 브랜치 삭제는 작업 이관 확인 후 별도 결정한다. 보호 설정은 과거 브랜치 삭제나 기록 재작성을 자동으로 수행하지 않는다.

## 참고한 공식 문서

- [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow): 보통은 변경 단위의 짧은 브랜치를 사용한다. 이번 팀은 사용자 결정에 따라 개인 브랜치를 유지한다.
- [오래 사용하는 브랜치의 merge 방식](https://docs.github.com/en/pull-requests/reference/pull-request-merges#squashing-and-merging-a-long-running-branch)
- [Git 훅](https://git-scm.com/docs/githooks)
- [GitHub ruleset](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
