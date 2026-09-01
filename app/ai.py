"""Server-side text-only adapter for an OpenRouter-compatible provider."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AIProviderError(RuntimeError):
    pass


class OpenRouterAdapter:
    def __init__(self, api_key: str | None = None, model: str | None = None, endpoint: str | None = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.model = model or os.environ.get("OPENROUTER_MODEL", "").strip()
        self.endpoint = endpoint or os.environ.get("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")

    def create_plan(self, source_filename: str, metadata: dict[str, Any], task: str) -> dict[str, Any]:
        if not self.api_key or not self.model:
            raise AIProviderError("AI provider is not configured")

        system = (
            "You create a plan for a private CutPilot video worker. Return JSON only, with exactly these fields: "
            "source_filename (string), commands (array of strings), summary (short string). "
            "commands may contain only: -mp4, -mov, -hevc, -1080p, -720p, -480p, -360p, -Nfps where N is 1..60, "
            "-Nmb or -Ngb for a positive integer, -nl, -nologo, -nc, -nocut, "
            "-crp-START-END, -crp=START-END, or -crp+START-END+START-END. "
            "Time uses dotted notation such as 4.15 or 01.23.23. Never output shell, FFmpeg, paths, URLs, "
            "or commands outside this list. If the task is ambiguous, choose no commands and explain briefly."
        )
        user = json.dumps(
            {"source_filename": source_filename, "file_metadata": metadata, "task": task},
            ensure_ascii=False,
        )
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8787",
                "X-Title": "CutPilot",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise AIProviderError("AI provider request failed") from exc
        try:
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not isinstance(content, str):
                raise KeyError("content")
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("plan is not an object")
            return result
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderError("AI provider returned an invalid plan") from exc
