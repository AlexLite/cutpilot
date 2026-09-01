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


if __name__ == "__main__":
    unittest.main()
