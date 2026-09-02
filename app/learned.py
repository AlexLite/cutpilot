"""Small durable dictionary of validated user phrases and CutPilot plans."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def normalize_phrase(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class LearnedDictionary:
    APPROVAL_USES = 2

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"version": 1, "entries": {}}
        return data if isinstance(data, dict) and isinstance(data.get("entries"), dict) else {"version": 1, "entries": {}}

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def lookup(self, phrase: str) -> dict[str, Any] | None:
        entry = self._read()["entries"].get(normalize_phrase(phrase))
        if not isinstance(entry, dict) or entry.get("status") != "approved":
            return None
        commands = entry.get("commands")
        if not isinstance(commands, list) or not all(isinstance(command, str) for command in commands):
            return None
        return {"commands": commands, "summary": entry.get("summary", "Локальный план из словаря")}

    def record(self, phrase: str, commands: tuple[str, ...], summary: str) -> str:
        key = normalize_phrase(phrase)
        data = self._read()
        entries = data["entries"]
        previous = entries.get(key)
        uses = previous.get("uses", 0) if isinstance(previous, dict) else 0
        if not isinstance(previous, dict) or previous.get("commands") != list(commands):
            uses = 0
        uses += 1
        status = "approved" if uses >= self.APPROVAL_USES else "pending"
        entries[key] = {"phrase": phrase.strip(), "commands": list(commands), "summary": summary, "uses": uses, "status": status}
        self._write(data)
        return status
