import unittest

from app.commands import CommandValidationError, build_staged_filename, build_worker_output_filename, validate_commands, validate_plan


class CommandTests(unittest.TestCase):
    def test_supported_commands_are_accepted(self):
        commands = validate_commands(["-mp4", "-1080p", "-25fps", "-nl", "-crp-4.15-4.36"])
        self.assertEqual(commands, ("-mp4", "-1080p", "-25fps", "-nl", "-crp-4.15-4.36"))

    def test_unknown_or_shell_commands_are_rejected(self):
        with self.assertRaises(CommandValidationError):
            validate_commands(["-mp4", "$(rm", "-rf", "/"])

    def test_invalid_edit_is_rejected(self):
        with self.assertRaises(CommandValidationError):
            validate_commands(["-crp=4.15-4.36+5.00-5.10"])

    def test_plan_name_is_deterministic(self):
        plan = validate_plan("interview.mov", {"source_filename": "interview.mov", "commands": ["-mp4", "-nl"], "summary": "Без логотипа"})
        self.assertEqual(plan.staged_filename, "interview [cmd -mp4 -nl].mp4")
        self.assertEqual(build_staged_filename("interview.mov", plan.commands), plan.staged_filename)

    def test_existing_command_block_is_not_reprocessed(self):
        with self.assertRaises(CommandValidationError):
            validate_plan("interview [cmd -mp4].mov", {"commands": []})

    def test_worker_output_name_matches_watcher(self):
        self.assertEqual(
            build_worker_output_filename("interview [cmd -mp4 -nl].mp4"),
            "interview_nologo.mp4",
        )

    def test_worker_reserved_names_and_path_separators_are_rejected(self):
        for filename in ("already_logo.mp4", "already_nologo.mp4", "a\\b.mp4", "a/b.mp4"):
            with self.assertRaises(CommandValidationError):
                validate_plan(filename, {"commands": []})


if __name__ == "__main__":
    unittest.main()
