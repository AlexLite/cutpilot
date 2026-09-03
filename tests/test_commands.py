import unittest

from app.commands import CommandValidationError, validate_edit_duration, validate_plan


class CommandNormalizationTests(unittest.TestCase):
    def test_normalizes_seconds_shorthand_from_ai(self):
        plan = validate_plan("clip.mp4", {"commands": ["-crp-0-10"], "summary": "cut"})
        self.assertEqual(plan.commands, ("-crp-00.00-00.10",))

    def test_normalizes_minute_values_above_99_to_hours(self):
        plan = validate_plan("clip.mp4", {"commands": ["-crp+166.04-181.04"], "summary": "cut"})
        self.assertEqual(plan.commands, ("-crp+02.46.04-03.01.04",))

    def test_rejects_unbounded_target_size(self):
        with self.assertRaises(CommandValidationError):
            validate_plan("clip.mp4", {"commands": ["-9999999gb"], "summary": "bad"})

    def test_accepts_mute_command(self):
        plan = validate_plan("clip.mp4", {"commands": ["-mute"], "summary": "mute"})
        self.assertEqual(plan.commands, ("-mute",))

    def test_rejects_edit_range_outside_duration(self):
        plan = validate_plan("clip.mp4", {"commands": ["-crp-00.00-01.10"], "summary": "cut"})
        with self.assertRaises(CommandValidationError):
            validate_edit_duration(plan.commands, 60)


if __name__ == "__main__":
    unittest.main()
