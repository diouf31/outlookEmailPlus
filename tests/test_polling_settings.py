import unittest

from tests._import_app import clear_login_attempts, import_web_app_module


class PollingSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def setUp(self):
        with self.app.app_context():
            clear_login_attempts()
            from outlook_web.repositories import settings as settings_repo

            settings_repo.set_setting("enable_auto_polling", "false")
            settings_repo.set_setting("polling_interval", "10")
            settings_repo.set_setting("polling_count", "5")

    def _login(self, client, password: str = "testpass123"):
        resp = client.post("/login", json={"username": "admin", "password": password})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("success"))

    def test_put_settings_round_trips_zero_polling_count(self):
        client = self.app.test_client()
        self._login(client)

        resp = client.put(
            "/api/settings",
            json={
                "enable_auto_polling": True,
                "polling_interval": 15,
                "polling_count": 0,
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("success"))

        resp2 = client.get("/api/settings")
        self.assertEqual(resp2.status_code, 200)
        settings = resp2.get_json().get("settings", {})
        self.assertTrue(settings.get("enable_auto_polling"))
        self.assertEqual(settings.get("polling_interval"), 15)
        self.assertEqual(settings.get("polling_count"), 0)

        with self.app.app_context():
            from outlook_web.repositories import settings as settings_repo
            from outlook_web.security.auth import get_current_user_id

            # 轮询次数写入当前用户偏好，而不是全局 settings
            # 在请求外无 user 上下文，直接按 admin 用户读取
            from outlook_web.db import get_db

            admin_id = get_db().execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
            self.assertEqual(settings_repo.get_user_setting(admin_id, "polling_count"), "0")

    def test_put_settings_accepts_positive_polling_count(self):
        client = self.app.test_client()
        self._login(client)

        resp = client.put(
            "/api/settings",
            json={
                "enable_auto_polling": True,
                "polling_interval": 20,
                "polling_count": 3,
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("success"))

        resp2 = client.get("/api/settings")
        settings = resp2.get_json().get("settings", {})
        self.assertEqual(settings.get("polling_interval"), 20)
        self.assertEqual(settings.get("polling_count"), 3)
