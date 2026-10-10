import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import ValidationError

from app.main import _aws_setup_url
from app.schemas import ConnectionCompleteIn, ConnectionRoleCallbackIn


class ConnectionInputTests(unittest.TestCase):
    def test_complete_requires_role_arn_account_to_match(self) -> None:
        valid = ConnectionCompleteIn(
            account_id="123456789012",
            role_arn="arn:aws:iam::123456789012:role/path/PavedCloudsReadOnlyRole",
        )
        self.assertEqual(valid.account_id, "123456789012")
        with self.assertRaises(ValidationError):
            ConnectionCompleteIn(
                account_id="123456789012",
                role_arn="arn:aws:iam::210987654321:role/PavedCloudsReadOnlyRole",
            )

    def test_complete_rejects_non_iam_role_arn(self) -> None:
        with self.assertRaises(ValidationError):
            ConnectionCompleteIn(
                account_id="123456789012",
                role_arn="arn:aws:sts::123456789012:assumed-role/Role/session",
            )

    def test_role_callback_validates_external_id_and_account_arn_pair(self) -> None:
        valid = ConnectionRoleCallbackIn(
            external_id="pc-" + "a" * 32,
            account_id="123456789012",
            role_arn="arn:aws:iam::123456789012:role/PavedCloudsReadOnlyRole",
        )
        self.assertEqual(valid.account_id, "123456789012")
        with self.assertRaises(ValidationError):
            ConnectionRoleCallbackIn(
                external_id="pc-" + "a" * 32,
                account_id="123456789012",
                role_arn="arn:aws:iam::210987654321:role/PavedCloudsReadOnlyRole",
            )

    def test_link_uses_operator_account_and_worker_region_environment(self) -> None:
        env = {
            "AWS_CONNECTION_TEMPLATE_URL": "https://templates.example.com/connection.yaml",
            "PLATFORM_AWS_ACCOUNT_ID": "123456789012",
            "AWS_REGION": "sa-east-1",
        }
        with patch.dict(os.environ, env, clear=True):
            setup_url = _aws_setup_url("pc-test-external-id")
        parsed_url = urlsplit(setup_url)
        self.assertEqual(parse_qs(parsed_url.query)["region"], ["sa-east-1"])
        query = parse_qs(parsed_url.fragment.split("?", maxsplit=1)[1])
        self.assertEqual(query["param_PlatformAccountId"], ["123456789012"])
        self.assertEqual(query["param_ExternalId"], ["pc-test-external-id"])

    def test_link_passes_role_callback_url_to_cloudformation(self) -> None:
        env = {
            "AWS_CONNECTION_TEMPLATE_URL": "https://templates.example.com/connection.yaml",
            "PUBLIC_API_BASE_URL": "https://api.example.com/base",
            "PLATFORM_AWS_ACCOUNT_ID": "123456789012",
            "AWS_REGION": "sa-east-1",
        }
        connection_id = "00000000-0000-4000-8000-000000000001"
        with patch.dict(os.environ, env, clear=True):
            setup_url = _aws_setup_url("pc-test-external-id", UUID(connection_id))
        query = parse_qs(urlsplit(setup_url).fragment.split("?", maxsplit=1)[1])
        self.assertEqual(
            query["param_RoleCallbackUrl"],
            [f"https://api.example.com/base/api/connections/{connection_id}/role-callback"],
        )


if __name__ == "__main__":
    unittest.main()
