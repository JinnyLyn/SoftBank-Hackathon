"""PR 본문의 필수 보고 형식을 검사한다. 본문은 데이터로만 읽고 출력하지 않는다."""

import argparse
import json
import os
from pathlib import Path
import re


SECTION = "컨텍스트·워크플로 변경 및 우회 보고 (필수)"
FIELDS = (
    "관련 문서·절", "실제 변경", "이유·근거", "영향·한계·후속 작업",
    "문서 처리", "미결 사항·확인할 담당자",
)
PLACEHOLDERS = {"", "-", "—", "...", "…", "todo", "tbd", "작성 필요", "미정"}


def visible_lines(body):
    # 주석과 코드 블록에 복사된 템플릿을 실제 보고로 인정하지 않는다.
    body = re.sub(r"<!--.*?(?:-->|\Z)", "", body, flags=re.S)
    fence = None
    for line in body.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        yield line


def validate(body):
    if not isinstance(body, str) or not body.strip():
        return ["PR 본문이 비어 있습니다. PR 템플릿을 작성하세요."]
    lines = list(visible_lines(body))
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((index, len(match[1]), match[2]))
    starts = [index for index, level, title in headings if level == 2 and title == SECTION]
    if len(starts) != 1:
        return [f"'## {SECTION}' 항목이 정확히 하나 필요합니다."]
    start = starts[0]
    end = next((index for index, level, _ in headings if index > start and level <= 2), len(lines))
    section = lines[start + 1:end]
    selected = []
    for line in section:
        match = re.match(r"^ {0,3}[-*+]\s+\[[xX]\]\s+(해당 없음|해당 있음)(?=\s|$|[—–-])", line)
        if match:
            selected.append(match[1])
    if len(selected) != 1:
        return ["보고 항목에서 '해당 없음'과 '해당 있음' 중 정확히 하나를 체크하세요."]
    if selected[0] == "해당 없음":
        return []

    reports = []
    report = {}
    current = None
    errors = []
    for line in section:
        match = re.match(r"^ {0,3}[-*+]\s+([^:]+):\s*(.*)$", line)
        label = match[1].replace("**", "").strip() if match else ""
        field = next((name for name in FIELDS if label.startswith(name)), None)
        if field:
            if field == FIELDS[0] and report:
                reports.append(report)
                report = {}
            if field in report:
                errors.append(f"상세 보고의 '{field}' 항목이 중복됐습니다.")
            report[field] = match[2]
            current = field
        elif re.match(r"^ {0,3}#{1,6}(?:\s|$)", line) or re.fullmatch(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*", line):
            current = None
        elif current:
            report[current] += "\n" + line
    if report:
        reports.append(report)
    if not reports:
        return ["'해당 있음'을 선택하면 템플릿의 상세 항목을 작성하세요."]
    for number, report in enumerate(reports, 1):
        for field in FIELDS:
            value = report.get(field, "").strip().strip("*` ").strip()
            if value.casefold() in PLACEHOLDERS:
                errors.append(f"상세 보고 {number}: '{field}' 항목을 작성하세요.")
            elif field == "문서 처리":
                treatment = re.match(r"^(이 PR에서 갱신|JinnyLyn 갱신 요청|변경 불필요)(?=\s|$|[—–:：-])(.*)$",
                                     value.replace("**", "").replace("`", ""), flags=re.S)
                reason = treatment[2].strip(" \t\r\n—–:：-") if treatment else ""
                if not treatment or reason.casefold() in PLACEHOLDERS or "중 선택하고" in value:
                    errors.append(f"상세 보고 {number}: '문서 처리'에서 처리 방법 하나와 수정 위치·내용 또는 이유를 적으세요.")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"), help="GitHub 이벤트 JSON")
    source.add_argument("--body-file", type=Path, help="로컬에서 확인할 PR 본문 파일")
    args = parser.parse_args()
    if not args.body_file and not args.event:
        parser.error("--body-file 또는 --event/GITHUB_EVENT_PATH가 필요합니다.")
    try:
        if args.body_file:
            body = args.body_file.read_text(encoding="utf-8")
        else:
            event = json.loads(Path(args.event).read_text(encoding="utf-8"))
            body = event["pull_request"]["body"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        print("FAIL: PR 본문 또는 pull_request 이벤트를 읽을 수 없습니다.")
        return 1
    errors = validate(body)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        print(".github/PULL_REQUEST_TEMPLATE.md와 WORKFLOW.md §4.1에 맞춰 PR 본문을 수정하세요.")
        return 1
    print("PASS: PR 컨텍스트·워크플로 보고 형식 확인 (내용의 사실성은 리뷰에서 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
