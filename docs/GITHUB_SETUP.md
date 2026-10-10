# GitHub 보호 설정과 팀원 설치

저장소 관리자: JinnyLyn. 설정 화면: [Rulesets](https://github.com/JinnyLyn/SoftBank-Hackathon/settings/rules).
2026-10-08에 개인 브랜치 5개와 아래 보호 규칙을 확인·설정했다.

## 서버 규칙

| 규칙 | 대상 | 동작 |
|---|---|---|
| `paved-main-pr-review` | 기본 브랜치 main | PR, 승인 1명, CODEOWNERS 승인, 새 커밋 시 승인 무효화, 대화 해결. 마지막 push의 독립 승인은 해제. merge commit만 허용. 삭제·강제 푸시 금지 |
| `paved-only-team-branches` | main·개인 5개 외 모든 브랜치 | 생성·갱신 금지. 우회 없음 |
| `paved-personal-branch-history` | 개인 5개 | 삭제·강제 푸시 금지. 우회 없음 |
| `paved-main-ci` | 기본 브랜치 main | Windows·Ubuntu Git guard 성공(훅 회귀·PR 보고 형식), 최신 main 기준 검사. 우회 없음 |

개인 브랜치: `JinnyLyn`, `JinVibe`, `Ophelia0419`, `totorosi`, `wisetg`.

`paved-main-pr-review`에만 JinnyLyn 계정의 **For pull requests only** 예외를 둔다. 본인 PR은 다른 팀원 승인 없이 검증·diff를 확인하고 PR 화면에서 우회 머지한다. 이 권한은 작성자 조건을 검사하지 않으므로 다른 사람 PR에도 쓸 수 있지만, 팀 운영에서는 본인 PR에만 사용한다. 명령행 main 직접 푸시는 이 예외로 허용되지 않는다.

CODEOWNERS는 main의 파일을 사용하며 기본 리뷰 담당은 JinnyLyn이다. 2026-10-10 재확인한 서버 설정은 `require_last_push_approval: false`다. JinnyLyn은 팀원 PR의 작은 수정을 GitHub 웹에서 반영한 뒤 최종 변경을 검토·승인할 수 있다. 승인 1명·CODEOWNERS 승인·새 커밋 시 승인 무효화·리뷰 대화 해결 규칙은 유지한다. 이 웹 편집 방식은 로컬의 타인 브랜치 커밋·푸시 제한을 해제하지 않는다.

## 필수 CI

`Team Git Guard`는 Git 훅의 허용·차단 동작과 PR 보고 검사 자체의 회귀를 검증한다. main 대상 PR에서는 컨텍스트·워크플로 보고 형식도 검사하며, 본문 수정 때 재실행한다. 이 두 필수 검사의 성공만으로 앱 동작이나 보고 내용의 사실성을 확인한 것은 아니다.

- `Git guard (ubuntu-latest)`
- `Git guard (windows-latest)`

PR 보고 검사는 기존 두 check 안에 포함하므로 서버 ruleset 변경이나 새 필수 check 등록 없이 적용한다. 누락된 보고는 PR 본문을 보완해 해결한다. 이 워크플로가 반영된 최신 main과 동기화한 뒤 사용하며, 구체적인 검사 범위와 로컬 명령은 [CI.md](CI.md)를 따른다.

2026-10-08 [설정 PR #3의 최초 CI](https://github.com/JinnyLyn/SoftBank-Hackathon/actions/runs/37748541823)에서 두 작업의 성공을 확인하고 `paved-main-ci` ruleset의 필수 status check로 등록했다. 대상은 기본 브랜치, 우회 목록은 비워 두고 최신 main 기준 검사를 요구한다. 이렇게 분리해 본인 PR의 리뷰를 생략해도 CI는 유지한다. 이후 새 검사를 도입할 때도 최초 성공을 확인한 뒤 필수로 등록한다.

Actions 권한은 `contents: read`이고 저장소 인증 정보는 checkout에 남기지 않는다. 외부 Action 버전은 전체 commit SHA로 고정한다. AWS 키·LLM 키를 사용하는 작업은 없다.

추가한 `Application CI`는 main 대상 PR과 main push에서 구성요소별 검사를 실행한다. 마지막 고정 check는 `Available app checks`다. MOCK 브라우저, 실제 샘플 DB/API/브라우저, 플랫폼 프런트 빌드, 플랫폼 백엔드 기동·HTTP 기본 검사, Terraform 정적 검사를 코드 유무에 따라 수행하고 전체 플랫폼 미검증을 요약에 남긴다. 자세한 범위는 [CI.md](CI.md)를 참고한다.

2026-10-10 재확인 시 `Available app checks`는 아직 서버의 필수 check로 등록되지 않았다. 초기 CI 조정은 워크플로와 문서를 갱신하며 기존 서버 ruleset을 변경하지 않는다. 앱 검사 실패도 머지 전에 해결하며, 필수 check로 등록할 때는 최초 원격 성공과 범위를 확인한 뒤 고정 이름 `Available app checks`를 사용한다. 조건부 개별 job은 미구현 상태에서 skip되므로 필수 check로 각각 등록하지 않는다. 실제 배포 권한·승인·rollback 방식은 별도 합의한다.

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
