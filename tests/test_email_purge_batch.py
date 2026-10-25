"""按关键词 / 清空邮件批量删除单元测试"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from tests._import_app import clear_login_attempts, import_web_app_module


class EmailPurgeBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def setUp(self):
        with self.app.app_context():
            clear_login_attempts()

        self.client = self.app.test_client()
        login = self.client.post("/login", json={"username": "admin", "password": "testpass123"})
        self.assertEqual(login.status_code, 200)

        unique = uuid.uuid4().hex[:8]
        self.email = f"purge_{unique}@outlook.com"
        with self.app.app_context():
            from outlook_web.db import get_db
            from outlook_web.security.crypto import encrypt_data

            db = get_db()
            cur = db.execute(
                """
                INSERT INTO accounts (
                    email, password, client_id, refresh_token,
                    account_type, provider, group_id, remark, status, user_id
                ) VALUES (?, ?, ?, ?, 'outlook', 'outlook', 1, '', 'active', 1)
                """,
                (
                    self.email,
                    encrypt_data("pwd"),
                    "client-id",
                    encrypt_data("refresh-token"),
                ),
            )
            self.account_id = cur.lastrowid
            db.commit()

    def tearDown(self):
        with self.app.app_context():
            from outlook_web.db import get_db

            db = get_db()
            db.execute("DELETE FROM accounts WHERE id = ?", (self.account_id,))
            db.commit()

    def test_purge_requires_keyword_in_keyword_mode(self):
        resp = self.client.post(
            "/api/emails/purge-batch",
            json={"account_ids": [self.account_id], "mode": "keyword", "keyword": ""},
        )
        self.assertEqual(resp.status_code, 400)

    def test_purge_by_keyword_deletes_matching_only(self):
        emails_page = {
            "success": True,
            "emails": [
                {"id": "m1", "subject": "Your Code 123", "bodyPreview": "hello"},
                {"id": "m2", "subject": "Hello", "bodyPreview": "no match"},
            ],
        }
        delete_ok = {"success": True, "success_count": 1, "failed_count": 0, "errors": []}

        with (
            patch(
                "outlook_web.controllers.emails.graph_service.get_emails_graph",
                return_value=emails_page,
            ),
            patch(
                "outlook_web.controllers.emails.graph_service.delete_emails_graph",
                return_value=delete_ok,
            ) as mock_del,
        ):
            resp = self.client.post(
                "/api/emails/purge-batch",
                json={
                    "account_ids": [self.account_id],
                    "mode": "keyword",
                    "keyword": "Code",
                    "folders": ["inbox"],
                },
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["summary"]["matched"], 1)
        self.assertEqual(data["summary"]["deleted"], 1)
        called_ids = mock_del.call_args[0][2]
        self.assertEqual(called_ids, ["m1"])

    def test_purge_all_collects_all_ids(self):
        emails_page = {
            "success": True,
            "emails": [
                {"id": "a1", "subject": "A", "bodyPreview": ""},
                {"id": "a2", "subject": "B", "bodyPreview": ""},
            ],
        }
        delete_ok = {"success": True, "success_count": 2, "failed_count": 0, "errors": []}

        with (
            patch(
                "outlook_web.controllers.emails.graph_service.get_emails_graph",
                return_value=emails_page,
            ),
            patch(
                "outlook_web.controllers.emails.graph_service.delete_emails_graph",
                return_value=delete_ok,
            ) as mock_del,
        ):
            resp = self.client.post(
                "/api/emails/purge-batch",
                json={
                    "account_ids": [self.account_id],
                    "mode": "all",
                    "folders": ["inbox"],
                },
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["summary"]["matched"], 2)
        self.assertEqual(data["summary"]["deleted"], 2)
        called_ids = mock_del.call_args[0][2]
        self.assertEqual(sorted(called_ids), ["a1", "a2"])

    def test_purge_skips_imap_accounts(self):
        with self.app.app_context():
            from outlook_web.db import get_db
            from outlook_web.security.crypto import encrypt_data

            db = get_db()
            cur = db.execute(
                """
                INSERT INTO accounts (
                    email, password, client_id, refresh_token,
                    account_type, provider, imap_host, imap_port, imap_password,
                    group_id, remark, status, user_id
                ) VALUES (?, '', '', '', 'imap', 'qq', 'imap.qq.com', 993, ?, 1, '', 'active', 1)
                """,
                (f"imap_{self.email}", encrypt_data("auth")),
            )
            imap_id = cur.lastrowid
            db.commit()

        try:
            resp = self.client.post(
                "/api/emails/purge-batch",
                json={"account_ids": [imap_id], "mode": "all", "folders": ["inbox"]},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["summary"]["skipped_imap"], 1)
            self.assertEqual(data["summary"]["deleted"], 0)
        finally:
            with self.app.app_context():
                from outlook_web.db import get_db

                db = get_db()
                db.execute("DELETE FROM accounts WHERE id = ?", (imap_id,))
                db.commit()


if __name__ == "__main__":
    unittest.main()
