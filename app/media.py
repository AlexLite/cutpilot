"""Small, bounded media probe used to improve AI planning context."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any


def _fps(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, denominator = value.split("/", 1)
    try:
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return round(result, 3) if result > 0 else None


def probe_media(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    video = next(stream for stream in streams if stream.get("codec_type") == "video")
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    format_data = data.get("format", {})
    result: dict[str, Any] = {
        "duration_seconds": format_data.get("duration"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _fps(video.get("avg_frame_rate")),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "container": format_data.get("format_name"),
    }
    return {key: value for key, value in result.items() if value is not None}
