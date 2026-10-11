"""도메인 선택(PUT /api/projects/{id}/domain)과 상태 응답의 domain. DB 없이 돈다(커서는 가짜).

실행: PYTHONPATH=back python -m unittest discover -s back/tests -p 'test_domain.py' -v
"""
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import _front_domain_status, app, save_project_domain
from app.schemas import DomainIn

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
HOST = "lpad0001.totorosi.cloud"


def fake_connect(project_exists=True):
    """connect()가 돌려주는 연결·커서 대역. 프로젝트 조회 결과만 정한다"""
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": str(PROJECT_ID)} if project_exists else None
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    return MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=connection), __exit__=MagicMock(return_value=False)))


class DomainChoiceTests(unittest.TestCase):
    def test_auto_returns_no_name_yet_and_no_cost(self) -> None:
        with patch("app.main.connect", fake_connect()):
            out = save_project_domain(PROJECT_ID, DomainIn(mode="auto"))
        self.assertEqual((out.mode, out.name, out.monthly_usd, out.records), ("auto", None, 0, []))
        self.assertIn("배포 ID", out.note)   # 이름은 worker가 계획 때 정한다는 안내

    def test_own_domain_is_not_supported_yet(self) -> None:
        with patch("app.main.connect", fake_connect()), self.assertRaises(HTTPException) as cm:
            save_project_domain(PROJECT_ID, DomainIn(mode="own", name="example.com"))
        self.assertEqual(cm.exception.status_code, 501)   # 프런트는 501이면 미리보기 주소로 진행하고 이유를 보여 준다

    def test_unknown_project_is_404(self) -> None:
        with patch("app.main.connect", fake_connect(project_exists=False)), self.assertRaises(HTTPException) as cm:
            save_project_domain(PROJECT_ID, DomainIn(mode="auto"))
        self.assertEqual(cm.exception.status_code, 404)

    def test_input_is_validated(self) -> None:
        for bad in [{"mode": "buy"}, {"mode": "auto", "extra": 1}, {"mode": "own", "name": "a" * 254}]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                DomainIn(**bad)

    def test_http_contract(self) -> None:
        schema = app.openapi()
        self.assertIn("put", schema["paths"]["/api/projects/{project_id}/domain"])
        self.assertIn("domain", schema["components"]["schemas"]["FrontDeployStatus"]["properties"])


class DomainStatusTests(unittest.TestCase):
    def events(self, *types: str) -> list[dict]:
        # 지금 worker는 healthy 보고 전에 주소를 확인한다: apply_done → domain_verified → healthy (실패면 healthy 없이 failed)
        return [{"event_type": t, "message": f"{t} 메시지"} for t in ("apply_done", *types, "healthy")]

    def test_no_domain_until_the_app_is_healthy(self) -> None:
        for status in ["queued", "provisioning", "deploying", "failed"]:
            with self.subTest(status=status):
                self.assertIsNone(_front_domain_status(status, {"domain": HOST}, self.events()))

    def test_deploy_without_a_domain_is_skipped(self) -> None:
        # 포트 방식(운영 환경에 자동 주소가 없음)과 로컬 Docker 계획에는 계획 변수 domain이 없다
        for variables in [{}, {"domain": None}, {"domain": ""}, None]:
            with self.subTest(variables=variables):
                s = _front_domain_status("healthy", variables, self.events())
                self.assertEqual((s.state, s.name), ("skipped", None))

    def test_healthy_without_a_check_record_still_waits(self) -> None:
        # 확인을 healthy 뒤에 하던 이전 worker의 배포. 확인 기록 없이 active라고 하지 않는다
        s = _front_domain_status("healthy", {"domain": HOST}, self.events())
        self.assertEqual((s.state, s.name, s.url), ("waiting_dns", HOST, None))

    def test_verified_domain_is_active_with_its_https_url(self) -> None:
        s = _front_domain_status("healthy", {"domain": HOST}, self.events("domain_verified"))
        self.assertEqual((s.state, s.url), ("active", f"https://{HOST}"))

    def test_failed_deploy_has_no_domain_status(self) -> None:
        # 지금 worker는 주소가 열리지 않으면 healthy 없이 domain_failed로 failed를 보고한다. 사유는 log에 있다
        self.assertIsNone(_front_domain_status("failed", {"domain": HOST}, self.events("domain_failed")))

    def test_failed_check_on_a_healthy_deploy_keeps_the_reason(self) -> None:
        # 이전 worker는 healthy 뒤에 domain_failed(경고)를 남겼다
        s = _front_domain_status("healthy", {"domain": HOST}, [*self.events(), {"event_type": "domain_failed", "message": "domain_failed 메시지"}])
        self.assertEqual((s.state, s.message), ("failed", "domain_failed 메시지"))

    def test_latest_domain_event_wins(self) -> None:
        s = _front_domain_status("healthy", {"domain": HOST}, [*self.events("domain_failed"), {"event_type": "domain_verified", "message": "x"}])
        self.assertEqual(s.state, "active")


if __name__ == "__main__":
    unittest.main()
