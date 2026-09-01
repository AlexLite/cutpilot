import unittest

from app.rules import simple_plan


class RulePlanTests(unittest.TestCase):
    def test_simple_request_does_not_need_ai(self):
        self.assertEqual(simple_plan("сделай mp4 1080p без логотипа"), {
            "commands": ["-mp4", "-1080p", "-nologo"],
            "summary": "Локальный план без запроса к AI",
        })

    def test_concat_ranges_are_normalized_locally(self):
        plan = simple_plan("склей 0.15-0.50+1.25-2.13 и оставь лого")
        self.assertEqual(plan["commands"], ["-crp+0.15-0.50+1.25-2.13"])

    def test_complex_request_falls_back_to_ai(self):
        self.assertIsNone(simple_plan("сделай как для телеграма, но сохрани качество"))


if __name__ == "__main__":
    unittest.main()
