from pathlib import Path
import tempfile
import unittest

from app.commands import ValidatedPlan
from app.storage import PlanStore


class StorageJobTests(unittest.TestCase):
    def test_job_history_survives_new_store_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cutpilot.db"
            plan = ValidatedPlan("clip.mp4", ("-nl",), "remove logo", "clip [cmd -nl].mp4")
            store = PlanStore(database)
            store.create_job("job-1", plan)
            store.update_job("job-1", "failed", "test failure")
            store.update_job_by_staged("clip [cmd -nl].mp4", "completed", "clip_nologo.mp4")

            reopened = PlanStore(database)
            jobs = reopened.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual({key: jobs[0][key] for key in ("id", "source", "staged_filename", "status", "message")}, {
                "id": "job-1", "source": "clip.mp4", "staged_filename": "clip [cmd -nl].mp4",
                "status": "completed", "message": "clip_nologo.mp4",
            })

    def test_corrupt_progress_values_do_not_break_history_read(self):
        from app.server import CutPilotService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress = root / ".cutpilot-progress"
            progress.mkdir()
            (progress / "clip.mp4.abc.progress").write_text("status=processing\nupdated_at=not-a-number\nduration=bad\n", encoding="utf-8")
            jobs = CutPilotService(ai=object(), ai_cut_directory=root / "AI_Cut", cutpilot_directory=root).jobs()
            self.assertEqual(jobs[0]["source"], "clip.mp4")
            self.assertEqual(jobs[0]["updated_at"], 0)


if __name__ == "__main__":
    unittest.main()
