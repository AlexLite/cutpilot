import unittest

from app.commands import validate_plan


class CommandNormalizationTests(unittest.TestCase):
    def test_normalizes_seconds_shorthand_from_ai(self):
        plan = validate_plan("clip.mp4", {"commands": ["-crp-0-10"], "summary": "cut"})
        self.assertEqual(plan.commands, ("-crp-00.00-00.10",))


if __name__ == "__main__":
    unittest.main()
