import tempfile
import unittest
from pathlib import Path

from app.commands import validate_plan
from app.jobs import JobError, handoff, source_metadata
from app.server import CutPilotService


class JobTests(unittest.TestCase):
    def test_confirmation_is_required_before_handoff(self):
        class FakeAI:
            def create_plan(self, source_filename, metadata, task):
                self.metadata = metadata
                return {"source_filename": source_filename, "commands": ["-nl"], "summary": "Без логотипа"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_cut = root / "AI_Cut"
            ai_cut.mkdir()
            (ai_cut / "sample.mp4").write_bytes(b"video")
            service = CutPilotService(FakeAI(), ai_cut, root / "cutpilot")
            plan = service.create_plan("sample.mp4", "без логотипа")
            self.assertEqual(plan["commands"], ["-nl"])
            self.assertEqual(service.ai.metadata, {"size_bytes": 5})
            with self.assertRaises(JobError):
                service.confirm(plan["plan_id"], False)
            result = service.confirm(plan["plan_id"], True)
            self.assertEqual(result["status"], "queued")

    def test_handoff_copies_atomically_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_cut = root / "AI_Cut"
            cutpilot = root / "cutpilot"
            ai_cut.mkdir()
            source = ai_cut / "sample.mov"
            source.write_bytes(b"video bytes")
            plan = validate_plan("sample.mov", {"commands": ["-mp4"], "summary": ""})
            handed = handoff(ai_cut, cutpilot, plan, source_metadata(ai_cut, "sample.mov"))
            self.assertEqual(handed, "sample [cmd -mp4].mp4")
            self.assertEqual(source.read_bytes(), b"video bytes")
            self.assertEqual((cutpilot / handed).read_bytes(), b"video bytes")

    def test_changed_source_is_not_handed_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_cut = root / "AI_Cut"
            ai_cut.mkdir()
            source = ai_cut / "sample.mp4"
            source.write_bytes(b"old")
            selected = source_metadata(ai_cut, "sample.mp4")
            source.write_bytes(b"new")
            plan = validate_plan("sample.mp4", {"commands": [], "summary": ""})
            with self.assertRaises(JobError):
                handoff(ai_cut, root / "cutpilot", plan, selected)

    def test_existing_worker_result_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_cut = root / "AI_Cut"
            cutpilot = root / "cutpilot"
            ai_cut.mkdir()
            cutpilot.mkdir()
            source = ai_cut / "sample.mp4"
            source.write_bytes(b"video bytes")
            (cutpilot / "sample_logo.mp4").write_bytes(b"existing result")
            plan = validate_plan("sample.mp4", {"commands": [], "summary": ""})
            with self.assertRaises(JobError):
                handoff(ai_cut, cutpilot, plan, source_metadata(ai_cut, "sample.mp4"))

    def test_expired_plan_cannot_be_confirmed(self):
        class FakeAI:
            def create_plan(self, source_filename, metadata, task):
                return {"source_filename": source_filename, "commands": [], "summary": ""}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_cut = root / "AI_Cut"
            ai_cut.mkdir()
            (ai_cut / "sample.mp4").write_bytes(b"video")
            service = CutPilotService(FakeAI(), ai_cut, root / "cutpilot")
            service.PENDING_TTL_SECONDS = 0
            plan = service.create_plan("sample.mp4", "обработать")
            with self.assertRaises(JobError):
                service.confirm(plan["plan_id"], True)


if __name__ == "__main__":
    unittest.main()
