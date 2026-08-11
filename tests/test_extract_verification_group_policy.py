from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from tests._import_app import clear_login_attempts, import_web_app_module


class ExtractVerificationGroupPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def setUp(self):
        with self.app.app_context():
            clear_login_attempts()

    def _login(self, client):
        resp = client.post("/login", json={"username": "admin", "password": "testpass123"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("success"))

    def _create_group_with_policy(
        self,
        *,
        length: str = "6-6",
        regex: str = "",
        ai_enabled: int = 0,
        ai_model: str = "",
    ) -> int:
        with self.app.app_context():
            from outlook_web.db import get_db

            db = get_db()
            cols = {str(r["name"]) for r in db.execute("PRAGMA table_info(groups)").fetchall()}
        required_cols = {
            "verification_code_length",
            "verification_code_regex",
            "verification_ai_enabled",
            "verification_ai_model",
        }
        self.assertTrue(
            required_cols.issubset(cols),
            f"groups 表缺少策略字段: {sorted(required_cols - cols)}",
        )

        with self.app.app_context():
            from outlook_web.db import get_db

            db = get_db()
            name = f"grp_pol_{uuid.uuid4().hex[:8]}"
            db.execute(
                """
                INSERT INTO groups (
                    name, description, color, proxy_url,
                    verification_code_length, verification_code_regex,
                    verification_ai_enabled, verification_ai_model
                ) VALUES (?, '', '#123456', '', ?, ?, ?, ?)
                """,
                (name, length, regex, int(ai_enabled), ai_model),
            )
            db.commit()
            row = db.execute("SELECT id FROM groups WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def _create_outlook_account(self, group_id: int) -> str:
        email_addr = f"{uuid.uuid4().hex}@extapi.test"
        with self.app.app_context():
            from outlook_web.db import get_db

            db = get_db()
            db.execute(
                """
                INSERT INTO accounts (
                    email, password, client_id, refresh_token,
                    group_id, status, account_type, provider
                ) VALUES (?, 'pw', 'cid-test', 'rt-test', ?, 'active', 'outlook', 'outlook')
                """,
                (email_addr, int(group_id)),
            )
            db.commit()
        return email_addr

    @staticmethod
    def _graph_email():
        return {
            "id": "msg-1",
            "subject": "Verification",
            "from": {"emailAddress": {"address": "noreply@example.com"}},
            "receivedDateTime": "2030-01-01T00:00:00Z",
            "isRead": False,
            "hasAttachments": False,
            "bodyPreview": "body preview",
        }

    @staticmethod
    def _graph_detail(body_text: str):
        return {
            "id": "msg-1",
            "subject": "Verification",
            "from": {"emailAddress": {"address": "noreply@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "user@example.com"}}],
            "receivedDateTime": "2030-01-01T00:00:00Z",
            "body": {"content": body_text, "contentType": "text"},
        }

    @patch("outlook_web.services.graph.get_email_detail_graph")
    @patch("outlook_web.services.graph.get_emails_graph")
    def test_web_extract_uses_default_6_digits_for_code(self, mock_get_emails_graph, mock_get_email_detail_graph):
        group_id = self._create_group_with_policy(length="6-6", regex="")
        email_addr = self._create_outlook_account(group_id)
        mock_get_emails_graph.return_value = {
            "success": True,
            "emails": [self._graph_email()],
        }
        mock_get_email_detail_graph.return_value = self._graph_detail("Your code is 123456")

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/emails/{email_addr}/extract-verification")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json().get("data", {})
        self.assertEqual(data.get("verification_code"), "123456")

    @patch("outlook_web.services.graph.get_email_detail_graph")
    @patch("outlook_web.services.graph.get_emails_graph")
    def test_web_extract_link_not_affected_by_default_6_digits(self, mock_get_emails_graph, mock_get_email_detail_graph):
        group_id = self._create_group_with_policy(length="6-6", regex="")
        email_addr = self._create_outlook_account(group_id)
        mock_get_emails_graph.return_value = {
            "success": True,
            "emails": [self._graph_email()],
        }
        mock_get_email_detail_graph.return_value = self._graph_detail(
            "Please click https://example.com/verify?token=abc (no six-digit code here)"
        )

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/emails/{email_addr}/extract-verification")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json().get("data", {})
        self.assertIn("verify", str(data.get("links") or ""))

    @patch("outlook_web.services.graph.get_email_detail_graph")
    @patch("outlook_web.services.graph.get_emails_graph")
    def test_web_extract_uses_group_regex_over_group_length(self, mock_get_emails_graph, mock_get_email_detail_graph):
        group_id = self._create_group_with_policy(length="6-6", regex=r"\b[A-Z]{4}\d{2}\b")
        email_addr = self._create_outlook_account(group_id)
        mock_get_emails_graph.return_value = {
            "success": True,
            "emails": [self._graph_email()],
        }
        mock_get_email_detail_graph.return_value = self._graph_detail("fallback 123456 but target CODE12")

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/emails/{email_addr}/extract-verification")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json().get("data", {})
        self.assertEqual(data.get("verification_code"), "CODE12")

    @patch("outlook_web.services.graph.get_email_detail_graph")
    @patch("outlook_web.services.graph.get_emails_graph")
    def test_web_extract_request_params_override_group_policy(self, mock_get_emails_graph, mock_get_email_detail_graph):
        group_id = self._create_group_with_policy(length="6-6", regex=r"\b[A-Z]{4}\d{2}\b")
        email_addr = self._create_outlook_account(group_id)
        mock_get_emails_graph.return_value = {
            "success": True,
            "emails": [self._graph_email()],
        }
        mock_get_email_detail_graph.return_value = self._graph_detail("target 1234 and CODE12")

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/emails/{email_addr}/extract-verification?code_length=4-4")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json().get("data", {})
        self.assertEqual(data.get("verification_code"), "1234")

    @patch("outlook_web.services.graph.get_emails_graph")
    def test_web_extract_group_ai_legacy_fields_do_not_block(self, mock_get_emails_graph):
        group_id = self._create_group_with_policy(length="6-6", regex="", ai_enabled=1, ai_model="")
        email_addr = self._create_outlook_account(group_id)
        mock_get_emails_graph.return_value = {
            "success": True,
            "emails": [self._graph_email()],
        }

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/emails/{email_addr}/extract-verification")

        # 不再因为 group 侧 AI 字段缺失报错；这里可能是未命中 404 或命中 200
        self.assertIn(resp.status_code, (200, 404))


if __name__ == "__main__":
    unittest.main()
