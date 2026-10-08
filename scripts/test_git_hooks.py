"""임시 로컬 저장소에서 실제 Git 명령으로 훅을 검증한다. 네트워크·실제 저장소 변경 없음."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="paved-git-test-") as tmp:
        base = Path(tmp)
        env = os.environ.copy()
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(base / "empty-config"), GIT_TERMINAL_PROMPT="0")
        (base / "empty-config").write_text("", encoding="utf-8")
        results = []

        def run(cwd, args, *, allowed=True):
            p = subprocess.run(args, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if (p.returncode == 0) != allowed:
                raise AssertionError(f"기대 결과와 다름: {args}\n{p.stdout}\n{p.stderr}")
            return p

        def git(cwd, *args, allowed=True):
            return run(cwd, ["git", *args], allowed=allowed)

        def ok(name):
            results.append(name)
            print("PASS " + name)

        seed, remote, repo = base / "seed", base / "remote.git", base / "clone with spaces"
        seed.mkdir()
        git(seed, "init", "-b", "main")
        git(seed, "config", "user.name", "Hook Test")
        git(seed, "config", "user.email", "hook-test@example.invalid")
        for part in [".githooks", "scripts", ".github"]:
            shutil.copytree(source / part, seed / part)
        (seed / "start.txt").write_text("start\n", encoding="utf-8")
        git(seed, "add", ".")
        git(seed, "commit", "-m", "seed")
        git(seed, "branch", "JinnyLyn")
        git(seed, "branch", "totorosi")
        git(base, "clone", "--bare", str(seed), str(remote))
        git(base, "clone", str(remote), str(repo))
        git(repo, "config", "user.name", "Hook Test")
        git(repo, "config", "user.email", "hook-test@example.invalid")
        # 기존 비배정 로컬 브랜치도 쓰기 차단되는지 확인하기 위해 설치 전에 만든다.
        git(repo, "branch", "totorosi", "origin/totorosi")
        run(repo, [sys.executable, "scripts/setup_git.py", "JinnyLyn"])
        run(repo, [sys.executable, "scripts/setup_git.py", "--check"])
        run(repo, [sys.executable, "scripts/setup_git.py", "JinnyLyn"])
        ok("공백 경로 설치·상태 확인·반복 설치")

        initial = git(repo, "rev-parse", "main").stdout.strip()
        git(repo, "commit", "--allow-empty", "-m", "forbidden main", allowed=False)
        git(repo, "commit", "--no-verify", "--allow-empty", "-m", "forbidden main skip", allowed=False)
        assert git(repo, "rev-parse", "main").stdout.strip() == initial
        ok("main 커밋 및 --no-verify 커밋 차단")
        git(repo, "branch", "feat/extra", allowed=False)
        git(repo, "switch", "-c", "extra", allowed=False)
        git(repo, "worktree", "add", "-b", "agent-extra", str(base / "extra-worktree"), allowed=False)
        git(repo, "show-ref", "--verify", "refs/heads/feat/extra", allowed=False)
        git(repo, "show-ref", "--verify", "refs/heads/extra", allowed=False)
        git(repo, "show-ref", "--verify", "refs/heads/agent-extra", allowed=False)
        ok("branch·switch -c·worktree의 추가 브랜치 생성 차단")

        git(repo, "switch", "--track", "origin/JinnyLyn")
        git(repo, "commit", "--allow-empty", "-m", "personal allowed")
        git(repo, "push", "origin", "JinnyLyn")
        ok("배정 브랜치 생성·커밋·푸시 허용")
        git(repo, "push", "origin", "HEAD:main", allowed=False)
        git(repo, "push", "origin", "HEAD:totorosi", allowed=False)
        git(repo, "push", "origin", "HEAD:extra-remote", allowed=False)
        git(repo, "push", "origin", ":JinnyLyn", allowed=False)
        ok("main·다른 팀원·추가 브랜치 푸시 및 개인 브랜치 삭제 차단")

        git(repo, "switch", "totorosi")
        git(repo, "commit", "--allow-empty", "-m", "other branch", allowed=False)
        git(repo, "switch", "--detach", "HEAD")
        git(repo, "commit", "--allow-empty", "-m", "detached", allowed=False)
        git(repo, "switch", "JinnyLyn")
        ok("다른 팀원 브랜치·분리된 HEAD 커밋 차단")
        git(repo, "branch", "-m", "JinnyLyn", "renamed", allowed=False)
        assert git(repo, "branch", "--show-current").stdout.strip() == "JinnyLyn"
        ok("개인 브랜치 개명 차단")

        # 별도 fixture가 원격 main을 진행시킨 뒤 fetch와 동기화가 가능한지 검사한다.
        git(seed, "remote", "add", "origin", str(remote))
        (seed / "new.txt").write_text("new\n", encoding="utf-8")
        git(seed, "add", "new.txt")
        git(seed, "commit", "-m", "upstream update")
        git(seed, "push", "origin", "main")
        git(repo, "fetch", "origin")
        git(repo, "merge", "--no-edit", "origin/main")
        git(repo, "push", "origin", "JinnyLyn")
        git(repo, "switch", "main")
        git(repo, "merge", "--ff-only", "origin/main")
        ok("fetch·개인 브랜치 main merge·로컬 main fast-forward 허용")
        git(repo, "switch", "JinnyLyn")
        git(repo, "reset", "--hard", "origin/main")
        git(repo, "push", "--force", "origin", "JinnyLyn", allowed=False)
        ok("개인 브랜치 강제 푸시 차단")

        # 훅 경로 설정 자체를 우회하면 로컬 강제를 보장할 수 없음을 명시적으로 확인한다.
        (base / "empty-hooks").mkdir()
        git(repo, "-c", "core.hooksPath=" + str(base / "empty-hooks"), "branch", "explicit-bypass")
        ok("로컬 설정 우회 한계 확인: GitHub 서버 규칙 병행 필요")
        print(f"\n총 {len(results)}개 시나리오 통과. GitHub 서버 규칙은 이 테스트의 검증 범위 밖입니다.")


if __name__ == "__main__":
    main()
