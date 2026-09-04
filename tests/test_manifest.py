import json
import tempfile
import unittest
from pathlib import Path

from app.manifest import commands_for_file, write_manifest


class ManifestTests(unittest.TestCase):
    def test_manifest_is_outside_media_and_resolves_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_manifest(root / "jobs", "job-1", "video.mp4", ("-nologo", "-nocut"))
            self.assertEqual(path.parent, root / "jobs")
            self.assertTrue(path.name.endswith(".json"))
            self.assertEqual(commands_for_file(root / "jobs", "video.mp4"), "-nologo -nocut")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["queue_filename"], "video.mp4")


if __name__ == "__main__":
    unittest.main()
