"""Helpers for structured jobs kept outside the shared media directory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_manifest(directory: Path, job_id: str, queue_filename: str, commands: tuple[str, ...]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{job_id}.json"
    temporary = directory / f".{job_id}.{os.getpid()}.tmp"
    payload: dict[str, Any] = {"job_id": job_id, "queue_filename": queue_filename, "commands": list(commands)}
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def commands_for_file(directory: Path, filename: str) -> str:
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("queue_filename") != filename:
            continue
        commands = data.get("commands")
        if isinstance(commands, list) and all(isinstance(item, str) for item in commands):
            return " ".join(commands)
    return ""


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        raise SystemExit(2)
    print(commands_for_file(Path(sys.argv[1]), sys.argv[2]))
