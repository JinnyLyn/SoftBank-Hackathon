"""체크아웃된 코드의 검사 범위와 CI 결과를 판정한다. 외부 패키지는 쓰지 않는다."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


CONTRACTS = {
    "sample_front": ("sample-front", ("index.html", "login.html", "signup.html", "app.js", "mock-api.js", "style.css")),
    "sample_back": ("sample-back", ("Dockerfile", "docker-compose.yml", "requirements.txt", "app/main.py")),
    "front": ("front", ("package.json", "package-lock.json", "src/App.tsx")),
    "infra": ("infra", ("bootstrap/versions.tf", "foundation/versions.tf", "deployments/_template/versions.tf", "modules/ecs-web-app/main.tf", "scripts/deploy.sh")),
}
IGNORED_DIRS = {"node_modules", ".venv", "__pycache__", ".terraform", "dist", "screenshots"}


def is_implementation(path):
    return not any(part in IGNORED_DIRS for part in path.parts) and path.name != ".gitkeep" and path.suffix.lower() != ".md"


def has_implementation(directory):
    if not directory.exists():
        return False
    for path in directory.rglob("*"):
        if path.is_file() and is_implementation(path.relative_to(directory)):
            return True
    return False


def inspect(root, previously_present=()):
    present = {}
    for key, (directory, required) in CONTRACTS.items():
        component = root / directory
        present[key] = has_implementation(component)
        if key in previously_present and not present[key]:
            raise ValueError(f"{directory}: 기존 구현이 사라졌습니다. 검사 생략으로 통과시킬 수 없습니다.")
        if key == "sample_front" or present[key]:
            missing = [name for name in required if not (component / name).is_file()]
            if missing:
                raise ValueError(f"{directory}: 검사 계약 파일 누락: {', '.join(missing)}. docs/CI.md를 함께 갱신하세요.")
    if has_implementation(root / "back"):
        raise ValueError("back/ 구현이 추가됐습니다. 플랫폼 백엔드의 실제 실행·통합 검사를 CI에 연결해야 합니다. docs/CI.md 참고.")
    return present


def previous_components(root, revision):
    # 이벤트의 SHA를 인자로 전달한다. 외부 문자열을 셸 코드로 실행하지 않는다.
    if not revision or set(revision) == {"0"}:
        return set()
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("CI_BASE_SHA에는 비교할 커밋 SHA가 필요합니다.")
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision], cwd=root, text=True,
    ).splitlines()
    return {
        key for key, (directory, _) in CONTRACTS.items()
        if any(path.startswith(directory + "/") and is_implementation(Path(path).relative_to(directory)) for path in paths)
    }


def report(needs):
    inventory = needs.get("inventory", {})
    failed = inventory.get("result") != "success"
    lines = ["## 앱 CI 검사 범위", "", "| 구성요소 | 결과 | 검증 범위 |", "|---|---|---|"]
    scopes = {
        "sample_front": "Chrome 사용자 흐름 · MOCK (실제 API/DB 아님)",
        "sample_back": "Docker 빌드 + MySQL 8.4 + 실제 API/브라우저 + DB 중단 감지",
        "front": "잠긴 의존성 설치 + TypeScript/Vite 빌드 (MOCK/실제 API 설정)",
        "infra": "Terraform fmt/init/validate + 셸 문법 (AWS plan/apply 아님)",
    }
    for key, scope in scopes.items():
        enabled = inventory.get("outputs", {}).get(key)
        result = needs.get(key, {}).get("result", "missing")
        expected = "success" if key == "sample_front" or enabled == "true" else "skipped"
        valid = enabled in ("true", "false") and result == expected and (key != "sample_front" or enabled == "true")
        failed |= not valid
        label = "미구현: 검사 안 함" if valid and expected == "skipped" else result
        lines.append(f"| {key} | {label} | {scope} |")
    lines += [
        "", f"**구현된 구성요소 검사: {'실패' if failed else '통과'}**",
        "", "**전체 플랫폼 통합: 미검증.** 플랫폼 백엔드·LLM·승인·실제 배포 연결은 아직 없습니다.",
        "이 결과는 전체 앱 정상이나 AWS 배포 성공을 뜻하지 않습니다. 검사 범위와 확장 방법은 docs/CI.md를 참고하세요.",
    ]
    if inventory.get("result") != "success":
        lines += ["", "구성요소 탐지 실패: inventory 작업의 누락 파일/검사 계약 오류를 확인하세요."]
    return failed, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "report"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if args.command == "inspect":
        present = inspect(args.root, previous_components(args.root, os.environ.get("CI_BASE_SHA")))
        print(json.dumps(present, indent=2))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                for key, enabled in present.items():
                    output.write(f"{key}={str(enabled).lower()}\n")
    else:
        failed, summary = report(json.loads(os.environ["CI_NEEDS"]))
        print(summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
                output.write(summary)
        raise SystemExit(int(failed))


if __name__ == "__main__":
    main()
