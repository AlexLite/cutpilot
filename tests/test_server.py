import unittest

from app.server import _correct_edit_intent, _correct_logo_intent


class LogoIntentTests(unittest.TestCase):
    def test_positive_logo_request_removes_no_logo_command(self):
        raw = {"commands": ["-nologo"], "summary": "remove"}
        self.assertEqual(_correct_logo_intent(raw, "просчитай с лого")["commands"], [])

    def test_negative_logo_request_is_not_changed(self):
        raw = {"commands": ["-nologo"], "summary": "remove"}
        self.assertEqual(_correct_logo_intent(raw, "убери логотип")["commands"], ["-nologo"])

    def test_concatenate_request_combines_edit_commands(self):
        raw = {"commands": ["-crp-00.15-00.50", "-crp-01.25-02.13", "-nl"], "summary": "cut"}
        corrected = _correct_edit_intent(raw, "склей 0.15-0.50+1.25-2.13 и наложи лого")
        self.assertEqual(corrected["commands"], ["-crp+00.15-00.50+01.25-02.13", "-nl"])


if __name__ == "__main__":
    unittest.main()
