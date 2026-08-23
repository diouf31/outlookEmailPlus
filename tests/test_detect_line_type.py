"""tests/test_detect_line_type.py — FD-00006 行类型识别单元测试"""

import unittest

from tests._import_app import import_web_app_module


class TestDetectLineType(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_web_app_module()
        cls.app = cls.module.app

    def _detect(self, line, fallback_host="", fallback_port=993):
        with self.app.app_context():
            from outlook_web.controllers.accounts import _detect_line_type

            return _detect_line_type(line, fallback_host, fallback_port)

    # --- Outlook ≥4 段 ---
    def test_outlook_4_parts(self):
        r = self._detect("user@outlook.com----pwd----client123----refresh_tok")
        self.assertEqual(r["type"], "outlook")
        self.assertEqual(r["provider"], "outlook")
        self.assertEqual(r["fields"]["email"], "user@outlook.com")
        self.assertEqual(r["fields"]["client_id"], "client123")
        self.assertEqual(r["fields"]["refresh_token"], "refresh_tok")

    def test_outlook_token_contains_separator(self):
        r = self._detect("u@o.com----p----cid----part1----part2----part3")
        self.assertEqual(r["type"], "outlook")
        self.assertEqual(r["fields"]["refresh_token"], "part1----part2----part3")

    def test_outlook_6_parts_with_aux_email(self):
        line = (
            "user@outlook.com----pwd----9e5f94bc-e8a4-4e73-b8be-63364c29d753"
            "----M.C514_BL2.token$$----aux@pyyemail.cc----AuxPass1"
        )
        r = self._detect(line)
        self.assertEqual(r["type"], "outlook")
        self.assertEqual(r["fields"]["email"], "user@outlook.com")
        self.assertEqual(r["fields"]["password"], "pwd")
        self.assertEqual(r["fields"]["client_id"], "9e5f94bc-e8a4-4e73-b8be-63364c29d753")
        self.assertEqual(r["fields"]["refresh_token"], "M.C514_BL2.token$$")
        self.assertEqual(r["fields"]["remark"], "辅助邮箱: aux@pyyemail.cc----AuxPass1")

    def test_outlook_token_contains_separator_and_aux_email(self):
        line = "u@o.com----p----cid----part1----part2----aux@mail.cc----auxpwd"
        r = self._detect(line)
        self.assertEqual(r["type"], "outlook")
        self.assertEqual(r["fields"]["refresh_token"], "part1----part2")
        self.assertEqual(r["fields"]["remark"], "辅助邮箱: aux@mail.cc----auxpwd")

    def test_imap_4_parts_host_port_detected_as_custom(self):
        r = self._detect("user@corp.com----pwd123----imap.corp.com----993")
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "custom")
        self.assertEqual(r["fields"]["imap_host"], "imap.corp.com")
        self.assertEqual(r["fields"]["imap_port"], 993)

    def test_imap_4_parts_invalid_port_returns_error_instead_of_outlook(self):
        r = self._detect("user@corp.com----pwd123----imap.corp.com----99999")
        self.assertEqual(r["type"], "error")
        self.assertIn("端口", r["error"])

    # --- Custom 5 段 ---
    def test_custom_5_parts(self):
        r = self._detect("user@corp.com----pwd123----custom----mail.corp.com----993")
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "custom")
        self.assertEqual(r["fields"]["imap_host"], "mail.corp.com")
        self.assertEqual(r["fields"]["imap_port"], 993)

    def test_custom_5_parts_case_insensitive(self):
        r = self._detect("u@c.com----p----Custom----h.com----995")
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "custom")

    def test_custom_5_parts_outlook_domain_requires_oauth(self):
        r = self._detect("user@outlook.com----pwd123----custom----mail.corp.com----993")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    def test_custom_5_parts_invalid_port_returns_error(self):
        r = self._detect("user@corp.com----pwd123----custom----mail.corp.com----99999")
        self.assertEqual(r["type"], "error")
        self.assertIn("端口", r["error"])

    def test_imap_4_parts_outlook_domain_requires_oauth(self):
        r = self._detect("user@outlook.com----pwd123----imap.corp.com----993")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    # --- IMAP 3 段 ---
    def test_imap_3_parts_known_provider(self):
        r = self._detect("user@qq.com----authcode----qq")
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "qq")
        self.assertEqual(r["fields"]["imap_host"], "imap.qq.com")

    def test_imap_3_parts_unknown_provider(self):
        r = self._detect("user@x.com----pwd----unknownprov")
        self.assertEqual(r["type"], "error")
        self.assertIn("未知", r["error"])

    def test_imap_3_parts_custom_not_allowed(self):
        r = self._detect("u@x.com----p----custom")
        self.assertEqual(r["type"], "error")

    def test_imap_3_parts_outlook_requires_oauth(self):
        r = self._detect("user@outlook.com----plain-password----outlook")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    # --- 2 段：域名推断 ---
    def test_2_parts_gmail_inferred(self):
        r = self._detect("user@gmail.com----apppassword")
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "gmail")
        self.assertEqual(r["fields"]["imap_host"], "imap.gmail.com")

    def test_2_parts_outlook_requires_oauth(self):
        r = self._detect("user@outlook.com----plain-password")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    def test_2_parts_hotmail_requires_oauth(self):
        r = self._detect("user@hotmail.com----plain-password")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    def test_2_parts_live_requires_oauth(self):
        r = self._detect("user@live.com----plain-password")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    def test_2_parts_live_cn_requires_oauth(self):
        r = self._detect("user@live.cn----plain-password")
        self.assertEqual(r["type"], "error")
        self.assertIn("OAuth", r["error"])

    def test_2_parts_unknown_with_fallback(self):
        r = self._detect("user@corp.com----pwd", fallback_host="mail.corp.com", fallback_port=995)
        self.assertEqual(r["type"], "imap")
        self.assertEqual(r["provider"], "custom")
        self.assertEqual(r["fields"]["imap_host"], "mail.corp.com")
        self.assertEqual(r["fields"]["imap_port"], 995)

    def test_2_parts_unknown_no_fallback(self):
        r = self._detect("user@corp.com----pwd")
        self.assertEqual(r["type"], "error")

    # --- 1 段：临时邮箱 ---
    def test_temp_mail_1_part(self):
        r = self._detect("temp@temp.example")
        self.assertEqual(r["type"], "temp_mail")
        self.assertEqual(r["provider"], "temp_mail")
        self.assertEqual(r["fields"]["email"], "temp@temp.example")

    def test_1_part_invalid_email(self):
        r = self._detect("not-an-email")
        self.assertEqual(r["type"], "error")


if __name__ == "__main__":
    unittest.main()
