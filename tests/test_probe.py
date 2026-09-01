import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class _ProbeResult:
    stdout = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "bit_rate": "1000"},
            {"codec_type": "audio", "bit_rate": "128000"},
        ],
        "format": {"duration": "12.5"},
    })


class ProbeTests(unittest.TestCase):
    def test_probe_uses_one_ffprobe_and_emits_normalized_fields(self):
        script = Path(__file__).parents[1] / "deploy" / "cutpilot-probe.py"
        spec = importlib.util.spec_from_file_location("cutpilot_probe", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.subprocess, "run", return_value=_ProbeResult()) as run, patch.object(module.sys, "argv", [str(script), "clip.mp4"]), patch("builtins.print") as output:
            self.assertEqual(module.main(), 0)
        values = dict(call.args[0].split("=", 1) for call in output.call_args_list)
        self.assertEqual(values, {"width": "1920", "height": "1080", "duration": "12.5", "vbitrate": "1000", "abitrate": "128000", "has_audio": "1"})
        run.assert_called_once()

    @unittest.skipIf(os.name == "nt", "POSIX fake executable is not portable to Windows")
    def test_probe_script_is_executable_with_fake_ffprobe(self):
        script = Path(__file__).parents[1] / "deploy" / "cutpilot-probe.py"
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "ffprobe"
            fake.write_text("#!/bin/sh\nprintf '%s' '{\"streams\":[{\"codec_type\":\"video\",\"width\":1,\"height\":1}],\"format\":{\"duration\":\"1\"}}'\n", encoding="utf-8")
            fake.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}{os.pathsep}{environment['PATH']}"
            completed = subprocess.run([sys.executable, str(script), "clip.mp4"], capture_output=True, text=True, env=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("has_audio=0", completed.stdout)


if __name__ == "__main__":
    unittest.main()
