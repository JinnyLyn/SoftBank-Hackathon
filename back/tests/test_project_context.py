from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.main import app
from app.schemas import GitHubProjectIn


class ProjectContextTests(unittest.TestCase):
    def test_http_contract_exposes_optional_context_for_both_sources(self) -> None:
        schema = app.openapi()
        zip_parameters = {
            parameter["name"]
            for parameter in schema["paths"]["/api/projects"]["post"].get("parameters", [])
        }
        expected = {"expected_users", "traffic_pattern", "monthly_budget_usd", "purpose"}
        self.assertTrue(expected.issubset(zip_parameters))
        github_properties = schema["components"]["schemas"]["GitHubProjectIn"]["properties"]
        project_properties = schema["components"]["schemas"]["ProjectOut"]["properties"]
        self.assertTrue(expected.issubset(github_properties))
        self.assertTrue(expected.issubset(project_properties))

    def test_accepts_frontend_scale_and_budget_values(self) -> None:
        project = GitHubProjectIn(
            name="sample",
            repository_url="https://github.com/owner/repo",
            ref="main",
            expected_users="~1,000",
            traffic_pattern="peak",
            monthly_budget_usd=30,
            purpose="동아리 출석 체크",
        )

        self.assertEqual(project.expected_users, "~1,000")
        self.assertEqual(project.traffic_pattern, "peak")
        self.assertEqual(project.monthly_budget_usd, Decimal("30"))
        self.assertEqual(project.purpose, "동아리 출석 체크")

    def test_scale_fields_remain_optional_for_existing_clients(self) -> None:
        project = GitHubProjectIn(name="sample", repository_url="https://github.com/owner/repo")

        self.assertIsNone(project.expected_users)
        self.assertIsNone(project.traffic_pattern)
        self.assertIsNone(project.monthly_budget_usd)
        self.assertIsNone(project.purpose)

    def test_rejects_unknown_scale_or_negative_budget(self) -> None:
        for values in (
            {"expected_users": "~500"},
            {"traffic_pattern": "bursty"},
            {"monthly_budget_usd": -1},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                GitHubProjectIn(
                    name="sample", repository_url="https://github.com/owner/repo", **values
                )

    def test_strips_blank_purpose_and_rejects_control_characters(self) -> None:
        base = {"name": "sample", "repository_url": "https://github.com/owner/repo"}
        self.assertIsNone(GitHubProjectIn(**base, purpose="   ").purpose)
        self.assertEqual(GitHubProjectIn(**base, purpose="line one\nline two").purpose, "line one\nline two")
        with self.assertRaises(ValidationError):
            GitHubProjectIn(**base, purpose="service\x00name")


if __name__ == "__main__":
    unittest.main()
