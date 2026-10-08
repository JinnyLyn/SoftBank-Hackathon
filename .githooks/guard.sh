#!/bin/sh
# 공통 Git 훅 함수. 외부 쉘 도구 없이 Git과 POSIX sh만 사용한다.
fail() {
    printf '%s\n' "[Paved Clouds] $*" >&2
    exit 1
}

load_assignment() {
    assigned=$(git config --local --get paved.assignedBranch) ||
        fail '개인 브랜치 설정이 없습니다. python scripts/setup_git.py <GitHub아이디> 를 실행하세요.'
    [ -n "$assigned" ] && [ "$assigned" != main ] || fail '개인 브랜치 설정이 잘못됐습니다.'
    git check-ref-format "refs/heads/$assigned" >/dev/null 2>&1 || fail '개인 브랜치 이름이 잘못됐습니다.'
}

require_assigned_head() {
    load_assignment
    current=$(git symbolic-ref --quiet --short HEAD) || fail '분리된 HEAD에서는 커밋·푸시하지 않습니다.'
    [ "$current" = "$assigned" ] || fail "현재 브랜치: $current. 작업은 배정 브랜치 $assigned 에서 하세요."
}

is_zero() {
    case "$1" in ''|*[!0]*) return 1 ;; *) return 0 ;; esac
}
