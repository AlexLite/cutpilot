import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.media import probe_media


class _Result:
    stdout = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "25/1", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    })


class MediaProbeTests(unittest.TestCase):
    def test_probe_returns_small_ai_safe_metadata(self):
        with patch("app.media.subprocess.run", return_value=_Result()) as run:
            result = probe_media(Path("clip.mp4"))
        self.assertEqual(result["width"], 1920)
        self.assertEqual(result["fps"], 25.0)
        self.assertEqual(result["audio_codec"], "aac")
        self.assertNotIn("streams", result)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
