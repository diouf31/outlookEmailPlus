import unittest
import uuid
from unittest.mock import patch

from tests._import_app import clear_login_attempts, import_web_app_module


class MaskingAuditAndImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def setUp(self):
        # 每个测试前清理登录限制记录，避免测试间互相影响
        with self.app.app_context():
            clear_login_attempts()

    def _login(self, client):
        resp = client.post("/login", json={"username": "admin", "password": "testpass123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get("success"), True)

    def _default_group_id(self) -> int:
        conn = self.module.create_sqlite_connection()
        try:
            row = conn.execute("SELECT id FROM groups WHERE name = '默认分组' LIMIT 1").fetchone()
            return row["id"] if row else 1
        finally:
            conn.close()

    def test_settings_get_does_not_leak_secrets(self):
        client = self.app.test_client()
        self._login(client)

        resp = client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("success"), True)

        settings = data.get("settings") or {}
        self.assertNotIn("login_password", settings)
        self.assertNotIn("gptmail_api_key", settings)
        self.assertNotIn("gptmail_api_key_set", settings)
        self.assertIn("login_password_set", settings)
        self.assertIn("temp_mail_api_key_set", settings)

    def test_account_get_returns_password_and_refresh_token_for_edit(self):
        unique = uuid.uuid4().hex
        email_addr = f"mask_{unique}@example.com"
        password = f"pass_{unique}"
        refresh_token = f"rt_{unique}"
        client_id = f"cid_{unique}"

        with self.app.app_context():
            from outlook_web.db import get_db
            from outlook_web.security.crypto import encrypt_data

            db = get_db()
            cur = db.execute(
                """
                INSERT INTO accounts (
                    email, password, client_id, refresh_token,
                    group_id, remark, status, user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    email_addr,
                    encrypt_data(password),
                    client_id,
                    encrypt_data(refresh_token),
                    self._default_group_id(),
                    "",
                    "active",
                ),
            )
            account_id = cur.lastrowid
            db.commit()

        client = self.app.test_client()
        self._login(client)
        resp = client.get(f"/api/accounts/{account_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("success"), True)

        account = data.get("account") or {}
        self.assertEqual(account.get("id"), account_id)
        self.assertEqual(account.get("email"), email_addr)
        self.assertEqual(account.get("password"), password)
        self.assertEqual(account.get("refresh_token"), refresh_token)
        self.assertEqual(account.get("has_password"), True)
        self.assertEqual(account.get("has_refresh_token"), True)

    def test_system_group_cannot_be_renamed(self):
        client = self.app.test_client()
        self._login(client)

        conn = self.module.create_sqlite_connection()
        try:
            row = conn.execute("SELECT id, name FROM groups WHERE is_system = 1 LIMIT 1").fetchone()
            self.assertIsNotNone(row)
            system_group_id = row["id"]
            system_group_name = row["name"]
        finally:
            conn.close()

        resp = client.put(
            f"/api/groups/{system_group_id}",
            json={
                "name": system_group_name + "_new",
                "description": "x",
                "color": "#111111",
            },
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data.get("success"), False)
        self.assertIsInstance(data.get("error"), dict)
        self.assertEqual(data["error"].get("code"), "SYSTEM_GROUP_PROTECTED")
        self.assertEqual(data["error"].get("status"), 403)

    def test_batch_import_returns_line_errors_and_parses_refresh_token_with_delimiter(
        self,
    ):
        client = self.app.test_client()
        self._login(client)

        unique = uuid.uuid4().hex
        email_addr = f"import_{unique}@example.com"
        client_id = f"client_{unique}"
        refresh_token = f"rt_{unique}----tail"

        account_string = "\n".join(
            [
                "bad_line_without_delimiter",
                f"{email_addr}----p----{client_id}----{refresh_token}",
            ]
        )

        resp = client.post(
            "/api/accounts",
            json={
                "account_string": account_string,
                "group_id": self._default_group_id(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("success"), True)

        summary = data.get("summary") or {}
        self.assertEqual(summary.get("imported"), 1)
        self.assertEqual(summary.get("failed"), 1)
        self.assertEqual(summary.get("total_lines"), 2)

        errors = data.get("errors") or []
        self.assertGreaterEqual(len(errors), 1)
        self.assertEqual(errors[0].get("line"), 1)

        conn = self.module.create_sqlite_connection()
        try:
            row = conn.execute(
                "SELECT refresh_token FROM accounts WHERE email = ? LIMIT 1",
                (email_addr,),
            ).fetchone()
            self.assertIsNotNone(row)
            stored = row["refresh_token"]
        finally:
            conn.close()

        stored_plain = self.module.decrypt_data(stored) if stored else stored
        self.assertEqual(stored_plain, refresh_token)

    def test_audit_logs_endpoint_includes_group_create(self):
        client = self.app.test_client()
        self._login(client)

        unique = uuid.uuid4().hex
        group_name = f"审计测试_{unique}"
        create = client.post(
            "/api/groups",
            json={
                "name": group_name,
                "description": "",
                "color": "#222222",
                "proxy_url": "",
            },
        )
        self.assertEqual(create.status_code, 200)
        created = create.get_json()
        self.assertEqual(created.get("success"), True)
        group_id = created.get("group_id")
        self.assertIsInstance(group_id, int)

        resp = client.get("/api/audit-logs?action=create&resource_type=group&limit=200")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("success"), True)

        logs = data.get("logs") or []
        matched = [r for r in logs if str(r.get("resource_id")) == str(group_id) and group_name in (r.get("details") or "")]
        self.assertTrue(matched)
        self.assertTrue(matched[0].get("trace_id"))

    def test_temp_email_actions_are_audited(self):
        client = self.app.test_client()
        self._login(client)

        unique = uuid.uuid4().hex
        email_addr = f"tmp_{unique}@example.com"

        # Mock legacy bridge 返回元组 (email_addr, None)
        # 当前正式入口经 temp mail provider 间接调用兼容 bridge，因此这里仍 patch gptmail 模块。
        from outlook_web.services import gptmail as gptmail_service

        with patch.object(gptmail_service, "generate_temp_email", return_value=(email_addr, None)):
            resp = client.post("/api/temp-emails/generate", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get("success"), True)

        create_audit = client.get("/api/audit-logs?resource_type=temp_email&action=create&limit=200")
        self.assertEqual(create_audit.status_code, 200)
        create_data = create_audit.get_json()
        self.assertEqual(create_data.get("success"), True)
        create_logs = create_data.get("logs") or []
        self.assertTrue([r for r in create_logs if r.get("resource_id") == email_addr])

        with patch.object(gptmail_service, "clear_temp_emails_from_api", return_value=True):
            conn = self.module.create_sqlite_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO temp_email_messages (message_id, email_address, subject, content, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("msg_" + unique, email_addr, "s", "c", 1),
                )
                conn.commit()
            finally:
                conn.close()

            clear_resp = client.delete(f"/api/temp-emails/{email_addr}/clear")
        self.assertEqual(clear_resp.status_code, 200)
        self.assertEqual(clear_resp.get_json().get("success"), True)

        audit_resp = client.get("/api/audit-logs?resource_type=temp_email_messages&action=delete&limit=200")
        self.assertEqual(audit_resp.status_code, 200)
        audit = audit_resp.get_json()
        self.assertEqual(audit.get("success"), True)

        logs = audit.get("logs") or []
        matched = [r for r in logs if (r.get("resource_id") == email_addr)]
        self.assertTrue(matched)
        self.assertTrue(matched[0].get("trace_id"))
