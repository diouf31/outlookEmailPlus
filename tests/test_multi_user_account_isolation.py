"""多用户同邮箱导入 + 删除全部 + 分页规格回归。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._import_app import clear_login_attempts, import_web_app_module


class MultiUserAccountIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def setUp(self):
        with self.app.app_context():
            clear_login_attempts()
            from outlook_web.db import get_db
            from outlook_web.repositories import users as users_repo

            db = get_db()
            db.execute("DELETE FROM account_claim_logs")
            db.execute("DELETE FROM account_project_usage")
            db.execute("DELETE FROM account_tags")
            db.execute("DELETE FROM accounts")
            db.execute("DELETE FROM users WHERE username != 'admin'")
            db.commit()

            existing = db.execute("SELECT id FROM users WHERE username = ?", ("a008",)).fetchone()
            if not existing:
                users_repo.create_user("a008", "testpass123", role="user")

            self.admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
            self.user_id = db.execute("SELECT id FROM users WHERE username = 'a008'").fetchone()["id"]
            self.admin_group_id = db.execute(
                "SELECT id FROM groups WHERE user_id = ? AND COALESCE(is_system, 0) = 0 ORDER BY id LIMIT 1",
                (self.admin_id,),
            ).fetchone()["id"]
            self.user_group_id = db.execute(
                "SELECT id FROM groups WHERE user_id = ? AND COALESCE(is_system, 0) = 0 ORDER BY id LIMIT 1",
                (self.user_id,),
            ).fetchone()["id"]

    def _login(self, client, username="admin", password="testpass123"):
        resp = client.post("/login", json={"username": username, "password": password})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json().get("success"))

    def _import_line(self, client, email: str, group_id: int):
        account_string = f"{email}----pwd----cid----refresh-token-value"
        return client.post(
            "/api/accounts",
            json={
                "account_string": account_string,
                "provider": "outlook",
                "group_id": group_id,
            },
        )

    def test_different_users_can_import_same_email(self):
        email = "shared-dup@example.com"
        with self.app.test_client() as client:
            self._login(client, "admin")
            resp_admin = self._import_line(client, email, self.admin_group_id)
            body = resp_admin.get_json() or {}
            self.assertEqual(resp_admin.status_code, 200, body)
            self.assertTrue(body.get("success"), body)
            self.assertGreaterEqual(int((body.get("summary") or {}).get("imported") or body.get("imported") or 0), 1)

        with self.app.test_client() as client:
            self._login(client, "a008")
            resp_user = self._import_line(client, email, self.user_group_id)
            body = resp_user.get_json() or {}
            self.assertEqual(resp_user.status_code, 200, body)
            self.assertTrue(body.get("success"), body)
            self.assertGreaterEqual(int((body.get("summary") or {}).get("imported") or body.get("imported") or 0), 1)

        with self.app.app_context():
            from outlook_web.db import get_db

            rows = get_db().execute(
                "SELECT user_id FROM accounts WHERE email = ? ORDER BY user_id",
                (email,),
            ).fetchall()
            self.assertEqual(len(rows), 2)

    def test_same_user_duplicate_email_still_rejected(self):
        email = "same-user-dup@example.com"
        with self.app.test_client() as client:
            self._login(client, "admin")
            first = self._import_line(client, email, self.admin_group_id)
            self.assertTrue((first.get_json() or {}).get("success"))
            second = self._import_line(client, email, self.admin_group_id)
            body = second.get_json() or {}
            summary = body.get("summary") or {}
            imported = int(summary.get("imported") or 0)
            failed = int(summary.get("failed") or 0)
            self.assertEqual(imported, 0)
            self.assertGreaterEqual(failed, 1)

    def test_page_size_allows_100_500_1000(self):
        with self.app.test_client() as client:
            self._login(client, "admin")
            for size in (100, 500, 1000):
                resp = client.get(f"/api/accounts?page=1&page_size={size}")
                self.assertEqual(resp.status_code, 200, resp.get_json())
                body = resp.get_json()
                self.assertTrue(body.get("success"))
                self.assertEqual(body.get("pagination", {}).get("page_size"), size)

    def test_delete_all_accounts_for_current_user(self):
        with self.app.test_client() as client:
            self._login(client, "admin")
            for i in range(3):
                resp = self._import_line(client, f"del-all-{i}@example.com", self.admin_group_id)
                self.assertTrue((resp.get_json() or {}).get("success"), resp.get_json())

        with self.app.test_client() as client:
            self._login(client, "a008")
            resp = self._import_line(client, "del-all-0@example.com", self.user_group_id)
            self.assertTrue((resp.get_json() or {}).get("success"), resp.get_json())

        with self.app.test_client() as client:
            self._login(client, "admin")
            resp = client.post("/api/accounts/delete-all", json={"confirm": "delete_all"})
            self.assertEqual(resp.status_code, 200, resp.get_json())
            body = resp.get_json()
            self.assertTrue(body.get("success"))
            self.assertGreaterEqual(int(body.get("deleted_count") or 0), 3)

        with self.app.app_context():
            from outlook_web.db import get_db

            admin_left = get_db().execute(
                "SELECT COUNT(*) AS c FROM accounts a JOIN users u ON a.user_id = u.id WHERE u.username = 'admin'"
            ).fetchone()["c"]
            other_left = get_db().execute(
                "SELECT COUNT(*) AS c FROM accounts a JOIN users u ON a.user_id = u.id WHERE u.username = 'a008'"
            ).fetchone()["c"]
            self.assertEqual(admin_left, 0)
            self.assertGreaterEqual(other_left, 1)


if __name__ == "__main__":
    unittest.main()
