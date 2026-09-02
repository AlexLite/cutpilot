"""Server-side text-only adapter for an OpenRouter-compatible provider."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("cutpilot.ai")


class AIProviderError(RuntimeError):
    pass


class OpenRouterAdapter:
    def __init__(self, api_key: str | None = None, model: str | None = None, endpoint: str | None = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.model = model or os.environ.get("OPENROUTER_MODEL", "").strip()
        self.endpoint = endpoint or os.environ.get("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")

    def create_plan(self, source_filename: str, metadata: dict[str, Any], task: str, feedback: str | None = None) -> dict[str, Any]:
        if not self.api_key or not self.model:
            raise AIProviderError("AI provider is not configured")

        system = (
            "Return JSON only: {commands:[string],summary:string}. Allowed commands: "
            "-mp4|-mov|-hevc|-1080p|-720p|-480p|-360p|-<number>fps (1..60)|-<number>mb|-<number>gb (positive), "
            "-nl|-nologo|-nc|-nocut, -crp-START-END, -crp=START-END, "
            "-crp+START-END+START-END. Use MM.SS or HH.MM.SS with two-digit seconds: "
            "the first 10 seconds is exactly -crp-00.00-00.10; never use -crp-0-10 or colons. "
            "Never return placeholders such as N, START, END, <number>, or angle brackets literally. "
            "Size examples: 'сожми до 100 Мб' -> '-100mb'; 'сожми до 2 Гб' -> '-2gb'. "
            "Logo semantics: 'с лого' or 'оставь логотип' means keep the existing logo and emit neither -nl nor -nologo; "
            "'наложи/добавь логотип' means emit -nl; 'без лого' or 'убери логотип' means emit -nologo. "
            "Edit example: 'обрежь 1.25-1.55' -> '-crp-01.25-01.55'. "
            "If the user object contains 'e', it is the previous validation error: return a corrected plan, not an explanation. "
            "No shell, FFmpeg, paths, URLs, or other commands. Ambiguous task: commands=[] and brief summary. "
            "summary <=160 characters."
        )
        user = json.dumps(
            {"n": source_filename, "m": metadata, "t": task, **({"e": feedback} if feedback else {})},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 160,
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
        if os.environ.get("CUTPILOT_AI_LOG_PAYLOAD", "0").lower() not in {"0", "false", "no"}:
            logger.info("AI request: model=%s endpoint=%s payload=%s", self.model, self.endpoint, payload.decode("utf-8"))
        try:
            with urlopen(request, timeout=45) as response:
                response_body = response.read(1_000_000).decode("utf-8")
                logger.info(
                    "AI response: status=%s body=%s",
                    getattr(response, "status", getattr(response, "code", "unknown")),
                    response_body,
                )
                data = json.loads(response_body)
        except HTTPError as exc:
            error_body = exc.read(1_000_000).decode("utf-8", errors="replace")
            logger.error("AI HTTP error: status=%s body=%s", exc.code, error_body)
            raise AIProviderError("AI provider request failed") from exc
        except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            logger.error("AI request error: %s", exc)
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
            logger.info("AI parsed plan: %s", result)
            return result
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error("AI response parse error: %s; content=%r", exc, content if "content" in locals() else None)
            raise AIProviderError("AI provider returned an invalid plan") from exc
