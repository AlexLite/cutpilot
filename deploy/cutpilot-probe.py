#!/usr/bin/env python3
"""Emit the media fields consumed by the Bash worker after one ffprobe call."""

from __future__ import annotations

import json
import subprocess
import sys


def numeric(stream: dict, key: str, default: str) -> str:
    value = stream.get(key)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value:
        try:
            float(value)
            return value
        except ValueError:
            pass
    return default


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", sys.argv[1]],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(completed.stdout)
        streams = data.get("streams", [])
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        format_data = data.get("format", {})
        values = {
            "width": numeric(video, "width", ""),
            "height": numeric(video, "height", ""),
            "duration": numeric(format_data, "duration", numeric(video, "duration", "")),
            "vbitrate": numeric(video, "bit_rate", "0"),
            "abitrate": numeric(audio, "bit_rate", "192000"),
            "has_audio": "1" if audio else "0",
        }
        for key, value in values.items():
            print(f"{key}={value}")
        return 0
    except (OSError, subprocess.CalledProcessError, StopIteration, TypeError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
