"""개인 브랜치용 Git 훅 설치. Python 표준 라이브러리만 사용한다."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def git(*args, cwd=None, required=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if required and result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git 명령 실패")
    return result


def main():
    parser = argparse.ArgumentParser(description="개인 브랜치용 Git 훅 설치/확인")
    parser.add_argument("username", nargs="?", help="배정된 GitHub 사용자명")
    parser.add_argument("--check", action="store_true", help="변경 없이 설치 상태 확인")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    script_root = Path(__file__).resolve().parents[1]
    if root != script_root:
        raise RuntimeError("설치할 저장소 루트에서 이 저장소의 scripts/setup_git.py를 실행하세요.")
    if git("rev-parse", "--is-bare-repository").stdout.strip() == "true":
        raise RuntimeError("작업용 clone에서 실행하세요.")
    version = re.search(r"(\d+)\.(\d+)", git("--version").stdout)
    if not version or tuple(map(int, version.groups())) < (2, 40):
        raise RuntimeError("이 설정은 Git 2.40 이상을 기준으로 합니다. Git을 업데이트하세요.")
    team = json.loads((root / ".github/team-branches.json").read_text(encoding="utf-8"))
    allowed = team["members"]
    source = root / ".githooks"
    common = Path(git("rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = root / common
    target = (common / "paved-hooks").resolve()
    expected = ["guard.sh", "pre-commit", "pre-merge-commit", "pre-push", "reference-transaction"]
    active = git("config", "--get", "core.hooksPath", required=False).stdout.strip()
    assigned = git("config", "--local", "--get", "paved.assignedBranch", required=False).stdout.strip()

    if args.check:
        problems = []
        if assigned not in allowed:
            problems.append("유효한 개인 브랜치 설정 없음")
        if active != target.as_posix():
            problems.append("훅 경로 불일치")
        for name in expected:
            path = target / name
            if not path.is_file() or path.read_bytes() != (source / name).read_bytes().replace(b"\r\n", b"\n"):
                problems.append(f"훅 누락 또는 구버전: {name}")
            elif os.name != "nt" and not os.access(path, os.X_OK):
                problems.append(f"실행 권한 없음: {name}")
        if problems:
            raise RuntimeError(" / ".join(problems))
        print(f"확인 완료: 개인 브랜치 {assigned}, Git 훅 설치됨")
        return

    if args.username not in allowed:
        raise RuntimeError("정확한 배정 사용자명을 지정하세요: " + ", ".join(allowed))
    git("check-ref-format", "refs/heads/" + args.username)
    if git("show-ref", "--verify", "--quiet", "refs/remotes/origin/" + args.username, required=False).returncode:
        raise RuntimeError("배정 원격 브랜치를 찾지 못했습니다. 먼저 git fetch origin을 실행하세요.")
    if assigned and assigned != args.username:
        raise RuntimeError(f"이미 {assigned}에 배정된 clone입니다. 다른 팀원 브랜치로 바꾸지 않습니다.")
    if active and active != target.as_posix():
        raise RuntimeError("기존 core.hooksPath가 있습니다. 기존 훅과 통합 여부를 검토한 뒤 설정하세요. 덮어쓰지 않았습니다.")
    if not active:
        conventional = common / "hooks"
        custom = [p.name for p in conventional.glob("*") if p.is_file() and not p.name.endswith(".sample")]
        if custom:
            raise RuntimeError("기존 Git 훅이 있습니다. 먼저 통합을 검토하세요: " + ", ".join(custom))
    for name in expected:
        if not (source / name).is_file():
            raise RuntimeError(f"훅 원본 없음: {name}")
    target.mkdir(parents=True, exist_ok=True)
    for name in expected:
        destination = target / name
        destination.write_bytes((source / name).read_bytes().replace(b"\r\n", b"\n"))
        destination.chmod(destination.stat().st_mode | 0o111)
    # 브랜치를 바꿔 원본 파일이 사라져도 훅이 계속 작동하도록 .git 안에 설치한다.
    git("config", "--local", "paved.assignedBranch", args.username)
    git("config", "--local", "core.hooksPath", target.as_posix())
    print(f"설치 완료: {args.username} 브랜치에서만 작업합니다.")
    print("커밋·브랜치 전환·푸시는 하지 않았습니다. WORKFLOW.md의 시작 절차를 따르세요.")
    print("새 clone에는 다시 설치해야 합니다. 훅 업데이트 후 같은 명령을 다시 실행하세요.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print("설정 실패: " + str(exc), file=sys.stderr)
        sys.exit(1)
