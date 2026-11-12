"""自动轮询设置按登录用户隔离。"""

from __future__ import annotations

import unittest

from tests._import_app import clear_login_attempts, import_web_app_module


class UserPollingSettingsIsolationTests(unittest.TestCase):
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
            db.execute("DELETE FROM user_settings")
            db.execute("DELETE FROM users WHERE username != 'admin'")
            db.commit()

            if not db.execute("SELECT id FROM users WHERE username = ?", ("a023",)).fetchone():
                users_repo.create_user("a023", "testpass123", role="user")

            self.admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
            self.user_id = db.execute("SELECT id FROM users WHERE username = 'a023'").fetchone()["id"]

    def _login(self, client, username="admin", password="testpass123"):
        resp = client.post("/login", json={"username": username, "password": password})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json().get("success"))

    def test_polling_settings_are_isolated_per_login_user(self):
        with self.app.test_client() as client:
            self._login(client, "admin")
            resp = client.put(
                "/api/settings",
                json={
                    "enable_auto_polling": True,
                    "polling_interval": 15,
                    "polling_count": 8,
                },
            )
            self.assertEqual(resp.status_code, 200, resp.get_json())
            self.assertTrue(resp.get_json().get("success"))

            got = client.get("/api/settings").get_json()["settings"]
            self.assertTrue(got["enable_auto_polling"])
            self.assertEqual(got["polling_interval"], 15)
            self.assertEqual(got["polling_count"], 8)

        with self.app.test_client() as client:
            self._login(client, "a023")
            got = client.get("/api/settings").get_json()["settings"]
            # 子账号默认不应跟随 admin 的开启状态
            self.assertFalse(got["enable_auto_polling"])

            resp = client.put(
                "/api/settings",
                json={
                    "enable_auto_polling": False,
                    "polling_interval": 30,
                    "polling_count": 2,
                },
            )
            self.assertEqual(resp.status_code, 200, resp.get_json())

            got = client.get("/api/settings").get_json()["settings"]
            self.assertFalse(got["enable_auto_polling"])
            self.assertEqual(got["polling_interval"], 30)
            self.assertEqual(got["polling_count"], 2)

        with self.app.test_client() as client:
            self._login(client, "admin")
            got = client.get("/api/settings").get_json()["settings"]
            self.assertTrue(got["enable_auto_polling"])
            self.assertEqual(got["polling_interval"], 15)
            self.assertEqual(got["polling_count"], 8)

        with self.app.app_context():
            from outlook_web.repositories import settings as settings_repo

            # 全局 settings 不应被用户偏好覆盖改写为子账号值
            self.assertNotEqual(settings_repo.get_setting("polling_interval"), "30")

            admin_poll = settings_repo.get_user_polling_settings(self.admin_id)
            user_poll = settings_repo.get_user_polling_settings(self.user_id)
            self.assertEqual(admin_poll["enable_auto_polling"], "true")
            self.assertEqual(user_poll["enable_auto_polling"], "false")
            self.assertEqual(user_poll["polling_interval"], "30")


if __name__ == "__main__":
    unittest.main()
