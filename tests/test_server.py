from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from app.jobs import JobError
from app.server import CutPilotService, _correct_edit_intent, _correct_logo_intent


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


class UploadTests(unittest.TestCase):
    def test_upload_writes_video_atomically_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CutPilotService(ai=object(), ai_cut_directory=root / "AI_Cut", cutpilot_directory=root)
            self.assertEqual(service.upload("sample.mp4", BytesIO(b"video-data"), 10), "sample.mp4")
            self.assertEqual((root / "AI_Cut" / "sample.mp4").read_bytes(), b"video-data")
            with self.assertRaises(JobError):
                service.upload("sample.mp4", BytesIO(b"other"), 5)


if __name__ == "__main__":
    unittest.main()
