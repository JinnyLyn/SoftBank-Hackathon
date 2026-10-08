# GitHub 보호 설정과 팀원 설치

저장소 관리자: JinnyLyn. 설정 화면: [Rulesets](https://github.com/JinnyLyn/SoftBank-Hackathon/settings/rules).
2026-10-08에 개인 브랜치 5개와 아래 보호 규칙을 확인·설정했다.

## 서버 규칙

| 규칙 | 대상 | 동작 |
|---|---|---|
| `paved-main-pr-review` | 기본 브랜치 main | PR, 승인 1명, CODEOWNERS 승인, 새 커밋 시 승인 무효화, 마지막 push의 독립 승인, 대화 해결. merge commit만 허용. 삭제·강제 푸시 금지 |
| `paved-only-team-branches` | main·개인 5개 외 모든 브랜치 | 생성·갱신 금지. 우회 없음 |
| `paved-personal-branch-history` | 개인 5개 | 삭제·강제 푸시 금지. 우회 없음 |

개인 브랜치: `JinnyLyn`, `JinVibe`, `Ophelia0419`, `totorosi`, `wisetg`.

`paved-main-pr-review`에만 JinnyLyn 계정의 **For pull requests only** 예외를 둔다. 본인 PR은 다른 팀원 승인 없이 검증·diff를 확인하고 PR 화면에서 우회 머지한다. 이 권한은 작성자 조건을 검사하지 않으므로 다른 사람 PR에도 쓸 수 있지만, 팀 운영에서는 본인 PR에만 사용한다. 명령행 main 직접 푸시는 이 예외로 허용되지 않는다.

CODEOWNERS는 main의 파일을 사용한다. 초기 설정 PR이 합쳐지기 전에는 기존 CODEOWNERS가 적용되며, 합쳐진 뒤 기본 리뷰 담당이 JinnyLyn으로 통일된다. 다른 팀원 PR에 JinnyLyn이 마지막 커밋까지 직접 push하면 마지막 push 승인 규칙과 충돌할 수 있으므로, 수정 요청은 PR 작성자가 반영한다.

## 필수 CI

이 변경에 포함된 GitHub Actions는 Git 훅의 허용·차단 동작을 검증한다. 앱 테스트나 AWS 배포가 구현됐다는 뜻은 아니다.

- `Git guard (ubuntu-latest)`
- `Git guard (windows-latest)`

처음 PR에서 두 작업이 실제로 성공한 것을 확인한 뒤, 별도 `paved-main-ci` ruleset의 필수 status check로 등록한다. 대상은 기본 브랜치, 우회 목록은 비우고 최신 main 기준 검사를 요구한다. 이렇게 분리해야 본인 PR의 리뷰를 생략해도 CI는 유지된다. 최초 실행 전부터 존재하지 않는 검사명을 필수로 걸지 않는다.

Actions 권한은 `contents: read`이고 저장소 인증 정보는 checkout에 남기지 않는다. 외부 Action 버전은 전체 commit SHA로 고정한다. AWS 키·LLM 키를 사용하는 작업은 없다.

앱별 실행·테스트 명령이 생기면 해당 CI를 추가하고 정상 실행 후 필수 검사로 등록한다. 실제 배포 권한·승인·rollback 방식은 별도 합의한다.

## 각 팀원이 한 번 실행

설정 PR을 main에 합친 뒤 clone의 최신 main에서 실행한다. `내아이디`는 본인 GitHub 계정으로 바꾼다.

```text
git fetch origin
python scripts/setup_git.py 내아이디
python scripts/setup_git.py --check
git switch --track origin/내아이디
git merge origin/main
```

이미 로컬 개인 브랜치가 있으면 `git switch 내아이디`를 사용한다. 명령 실행 전 미커밋 변경을 확인하고 보존한다. Python 명령은 환경에 따라 `py -3` 또는 `python3`으로 바꾼다. Git 2.40 이상, Python 3.9 이상이 필요하다.

Git 훅은 Git이 실행하므로 Codex·Claude Code·IDE가 달라도 같은 clone에서는 동일하게 동작한다. 새 clone·다른 PC에는 다시 설치해야 한다. 브라우저/API로 한 변경에는 로컬 훅이 실행되지 않으므로 서버 규칙이 필요하다.

로컬 훅은 보안 경계가 아니다. Git 설정·훅 파일을 직접 바꾸면 우회할 수 있다. `--no-verify`만으로는 reference-transaction 검사를 피할 수 없지만 `core.hooksPath`를 바꾸면 피할 수 있다. 문서와 테스트도 이 한계를 숨기지 않는다.

## 유지보수

- 팀원 추가 시 관리자와 브랜치·규칙 예외·개인 이력 보호·`team-branches.json`·WORKFLOW를 함께 갱신한다. 규칙 때문에 임의 브랜치부터 생성할 수는 없다.
- 기본 브랜치 이름을 바꾸면 허용 목록의 main 예외도 함께 갱신한다.
- 훅 변경 후 각 clone에서 설치 스크립트를 다시 실행한다. `--check`로 누락·구버전을 확인한다.
- merge 후 자동 브랜치 삭제는 끈 상태로 유지한다. 재사용 브랜치는 삭제하지 않는다.
- 일반 collaborator 간 다른 개인 브랜치 수정까지 GitHub 계정 기준으로 차단하는 설정은 이번 범위에 포함하지 않았다. 로컬 훅·팀 규칙으로 자기 브랜치를 사용한다.
- 서버 규칙은 저장소 관리자가 수정할 수 있다. 관리 권한 보유자의 설정 변경 자체를 막는 장치는 아니다.

## 공식 근거

- [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow): 일반적으로 작업 단위의 짧은 브랜치를 사용한다. 이번 팀의 개인 브랜치 방식은 팀 결정에 맞춘 운영이다.
- [장기 브랜치의 squash merge 주의점](https://docs.github.com/en/pull-requests/reference/pull-request-merges#squashing-and-merging-a-long-running-branch)
- [Ruleset 규칙](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Ruleset 생성과 PR 전용 우회](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
- [Git 훅](https://git-scm.com/docs/githooks)
