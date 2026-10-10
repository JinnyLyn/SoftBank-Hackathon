"""작성·수정·복사 실수와 외부 입력을 PR 보고 검사가 올바르게 처리하는지 확인한다."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_pr_report import validate


ROOT = Path(__file__).resolve().parents[2]
HEADING = "## 컨텍스트·워크플로 변경 및 우회 보고 (필수)\n"
NONE = HEADING + "\n- [x] 해당 없음\n- [ ] 해당 있음\n"
DETAILS = """- 관련 문서·절 / 기존 규칙 또는 개발 방향: WORKFLOW.md §4.1의 수동 보고 확인
- 실제 변경·우회·대체 구현·생략·보류한 내용 (관련 코드·커밋): CI에 보고 형식 검사를 추가
- 이유·근거 (최신 요청·회의 결정·기술 제약 등, 결정과 제안 구분): 사용자 요청
- 영향·한계·후속 작업: 작성 여부만 검사하고 사실성은 리뷰에서 확인
- 문서 처리: 이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영
- 미결 사항·확인할 담당자 (없으면 해당 없음): 해당 없음
"""
YES = HEADING + "\n- [ ] 해당 없음\n- [x] 해당 있음\n\n" + DETAILS


class ReportTests(unittest.TestCase):
    def test_explicit_none_and_completed_report_pass(self):
        for body in (NONE, YES, YES.replace("[x]", "[X]"), YES.replace("\n", "\r\n")):
            with self.subTest(body=body):
                self.assertEqual(validate(body), [])

    def test_empty_body_missing_heading_and_duplicate_heading_fail(self):
        for body in (None, 1, "", "\n", "- [x] 해당 없음", NONE + NONE):
            with self.subTest(body=body):
                self.assertTrue(validate(body))

    def test_requires_exactly_one_checked_choice(self):
        for body in (NONE.replace("[x]", "[ ]"), NONE.replace("[ ]", "[x]"),
                     NONE + "- [x] 해당 없음\n", NONE.replace("해당 없음", "해당 없음인지 미정")):
            with self.subTest(body=body):
                self.assertTrue(validate(body))

    def test_choices_and_fields_outside_report_do_not_count(self):
        for body in (HEADING + "\n## 검증\n- [x] 해당 없음",
                     HEADING + "\n- [x] 해당 있음\n# 다른 항목\n" + DETAILS):
            with self.subTest(body=body):
                self.assertTrue(validate(body))

    def test_hidden_templates_do_not_count(self):
        for body in (f"<!--\n{NONE}\n-->", f"```markdown\n{NONE}\n```",
                     f"~~~~\n{NONE}\n~~~~", "\n".join("    " + line for line in NONE.splitlines())):
            with self.subTest(body=body):
                self.assertTrue(validate(body))
        self.assertEqual(validate(NONE + "\n<!-- - [x] 해당 있음 -->"), [])
        self.assertEqual(validate(NONE + "\n```\n- [x] 해당 있음\n```"), [])

    def test_checked_yes_requires_every_detail_field(self):
        for line in DETAILS.splitlines():
            with self.subTest(line=line):
                self.assertTrue(validate(YES.replace(line, "")))
                self.assertTrue(validate(YES.replace(line, line.split(":", 1)[0] + ":")))
        self.assertTrue(validate(HEADING + "\n- [x] 해당 있음\n"))

    def test_placeholders_are_not_details(self):
        for placeholder in ("TODO", "TBD", "...", "미정", "**TODO**", "<!-- 실제 내용 -->"):
            with self.subTest(placeholder=placeholder):
                self.assertTrue(validate(YES.replace("사용자 요청", placeholder)))

    def test_leading_todo_or_tbd_cannot_fill_any_detail(self):
        placeholders = ("TODO: 추후 작성", "TBD - 담당자 확인", "todo： 추후 작성",
                        "TbD—담당자 확인", "**TODO**: 추후 작성", "`TBD` - 담당자 확인",
                        "\n  TODO: 추후 작성", "TODO\n담당자 확인")
        for line in DETAILS.splitlines():
            label, _ = line.split(":", 1)
            for placeholder in placeholders:
                with self.subTest(field=label, placeholder=placeholder):
                    self.assertTrue(validate(YES.replace(line, label + ": " + placeholder)))

    def test_document_treatment_reason_rejects_leading_placeholder(self):
        original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
        for treatment in ("이 PR에서 갱신", "JinnyLyn 갱신 요청", "변경 불필요"):
            for placeholder in ("TODO: 추후 작성", "TBD - 담당자 확인", "**TODO**: 추후 작성"):
                with self.subTest(treatment=treatment, placeholder=placeholder):
                    self.assertTrue(validate(YES.replace(original, treatment + " — " + placeholder)))

    def test_placeholder_mentions_and_similar_filenames_remain_valid(self):
        for explanation in ("기존 TODO 항목을 해결한 변경", "TODO.md의 실행 방법 수정",
                            "TODOS 목록 정리", "TBD_POLICY 설정 변경", "미정: API 담당자 확인이 필요함"):
            with self.subTest(explanation=explanation):
                self.assertEqual(validate(YES.replace("사용자 요청", explanation)), [])
                original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
                self.assertEqual(validate(YES.replace(original, "이 PR에서 갱신 — " + explanation)), [])

    def test_each_repeated_report_must_be_complete(self):
        self.assertEqual(validate(YES + "\n" + DETAILS), [])
        incomplete = DETAILS.replace("- 문서 처리: 이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영\n", "")
        self.assertTrue(validate(YES + "\n" + incomplete))
        self.assertTrue(validate(YES + "- 이유·근거: 중복\n"))

    def test_multiline_detail_and_subheading_are_supported(self):
        body = YES.replace("사용자 요청", "\n  사용자 요청에 따라 추가")
        body = body.replace(DETAILS.splitlines()[0], "### 항목 1\n" + DETAILS.splitlines()[0])
        self.assertEqual(validate(body), [])

    def test_subheading_and_separator_cannot_fill_an_empty_detail(self):
        incomplete = YES.replace("담당자 (없으면 해당 없음): 해당 없음", "담당자 (없으면 해당 없음):")
        for separator in ("### 항목 2", "---", "* * *"):
            with self.subTest(separator=separator):
                self.assertTrue(validate(incomplete + "\n" + separator + "\n" + DETAILS))

    def test_document_treatment_requires_choice_and_explanation(self):
        original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
        for value in ("TODO 이후 결정", "변경 불필요", "이 PR에서 갱신: TODO", "JinnyLyn 갱신 요청 —",
                      "이 PR에서 갱신 / JinnyLyn 갱신 요청 / 변경 불필요 중 선택하고, 이유 작성:"):
            with self.subTest(value=value):
                self.assertTrue(validate(YES.replace(original, value)))
        for value in ("**이 PR에서 갱신** — WORKFLOW.md 수정", "변경 불필요: 임시 우회는 기존 지침 범위 안임",
                      "JinnyLyn 갱신 요청 — AGENTS.md의 CI 검사 범위"):
            with self.subTest(value=value):
                self.assertEqual(validate(YES.replace(original, value)), [])

    def test_document_treatment_accepts_parenthetical_explanation(self):
        original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
        for treatment in ("이 PR에서 갱신", "JinnyLyn 갱신 요청", "변경 불필요"):
            for opening, closing in (("(", ")"), ("（", "）")):
                for separator in ("", " ", " — "):
                    value = treatment + separator + opening + "기존 지침 범위 안임" + closing
                    with self.subTest(value=value):
                        self.assertEqual(validate(YES.replace(original, value)), [])

    def test_document_treatment_parentheses_cannot_hide_missing_reason(self):
        original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
        for treatment in ("이 PR에서 갱신", "JinnyLyn 갱신 요청", "변경 불필요"):
            for opening, closing in (("(", ")"), ("（", "）")):
                for separator in ("", " ", " — "):
                    for reason in ("", " ", "TODO", "TBD", "TODO: 추후 작성", "TBD - 확인",
                                   "**TODO**", "`TBD`", "미정", "..."):
                        value = treatment + separator + opening + reason + closing
                        with self.subTest(value=value):
                            self.assertTrue(validate(YES.replace(original, value)))

    def test_parenthesis_support_preserves_treatment_and_field_names(self):
        original = "이 PR에서 갱신 — WORKFLOW.md와 docs/CI.md에 실행 방법 반영"
        self.assertTrue(validate(YES.replace(original, "변경 불필요함(기존 지침 범위 안임)")))
        self.assertTrue(validate(YES.replace("영향·한계·후속 작업:", "영향·한계·후속:")))

    def test_current_template_can_be_filled_without_changing_labels(self):
        template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertTrue(validate(template))
        self.assertEqual(validate(template.replace("- [ ] 해당 없음", "- [x] 해당 없음")), [])
        yes = template.replace("- [ ] 해당 있음", "- [x] 해당 있음")
        self.assertTrue(validate(yes))
        for line in DETAILS.splitlines():
            label, _ = line.split(":", 1)
            yes = yes.replace(label + ":", line)
        self.assertEqual(validate(yes), [])


class CommandTests(unittest.TestCase):
    def run_checker(self, content, mode="event"):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.txt"
            path.write_text(content, encoding="utf-8")
            env = dict(os.environ, PYTHONIOENCODING="utf-8", GITHUB_EVENT_PATH=str(path))
            command = [sys.executable, str(ROOT / "scripts/ci/check_pr_report.py")]
            if mode == "body":
                command += ["--body-file", str(path)]
            return subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)

    def test_event_and_body_file_pass(self):
        for content, mode in ((json.dumps({"pull_request": {"body": YES}}), "event"), (NONE, "body")):
            result = self.run_checker(content, mode)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_bad_events_fail_without_echoing_input(self):
        for event in ("fake-secret-sentinel", "[]", "{}", '{"pull_request": null}',
                      json.dumps({"pull_request": {"body": None}}),
                      json.dumps({"pull_request": {"body": "fake-secret-sentinel"}})):
            result = self.run_checker(event)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("fake-secret-sentinel", result.stdout + result.stderr)

    def test_body_is_data_not_shell_or_workflow_commands(self):
        body = YES.replace("사용자 요청", "$(exit 9) `exit 9` ::error::fake-secret-sentinel")
        result = self.run_checker(json.dumps({"pull_request": {"body": body}}))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("fake-secret-sentinel", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
