"""Small local-only HTTP application for the CutPilot first release."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .ai import AIProviderError, OpenRouterAdapter
from .commands import CommandValidationError, ValidatedPlan, validate_plan
from .jobs import JobError, handoff, list_sources, source_metadata

logger = logging.getLogger("cutpilot.server")


def _correct_logo_intent(raw: Any, task: str) -> Any:
    """Prevent a positive Russian logo request from becoming a no-logo command."""
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return raw
    lowered = task.casefold()
    positive = bool(re.search(r"(?:с|добавь|добавить|оставь|оставить|наложи|наложить|нанеси|поставь|keep|with)\s*(?:лого|логотип)", lowered))
    negative = bool(re.search(r"(?:без|убери|убрать|удали|удалить|remove)\s*(?:лого|логотип)", lowered))
    if not positive or negative:
        return raw
    commands = [command for command in raw["commands"] if command not in {"-nl", "-nologo"}]
    if len(commands) != len(raw["commands"]):
        corrected = dict(raw)
        corrected["commands"] = commands
        logger.warning("Corrected logo intent: task=%r raw=%r corrected=%r", task, raw, corrected)
        return corrected
    return raw


def _correct_edit_intent(raw: Any, task: str) -> Any:
    """Turn a concatenate request into one validated crp+ command."""
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return raw
    if not re.search(r"(?:склей|склеить|соедини|соединить|объедини|объединить)", task.casefold()):
        return raw
    commands = raw["commands"]
    edit_indexes = [index for index, command in enumerate(commands) if isinstance(command, str) and command.startswith(("-crp-", "-crp=", "-crp+"))]
    if not edit_indexes:
        return raw
    ranges: list[str] = []
    for index in edit_indexes:
        ranges.extend(commands[index][5:].split("+"))
    corrected = dict(raw)
    corrected["commands"] = [command for index, command in enumerate(commands) if index not in edit_indexes]
    corrected["commands"].insert(min(edit_indexes), "-crp+" + "+".join(ranges))
    logger.warning("Corrected edit intent: task=%r raw=%r corrected=%r", task, raw, corrected)
    return corrected


class CutPilotService:
    PENDING_TTL_SECONDS = 30 * 60

    def __init__(self, ai: Any | None = None, ai_cut_directory: Path | None = None, cutpilot_directory: Path | None = None):
        self.ai = ai or OpenRouterAdapter()
        self.ai_cut_directory = Path(ai_cut_directory or os.environ.get("CUTPILOT_AI_CUT_DIRECTORY", "/srv/cutpilot/AI_Cut"))
        self.cutpilot_directory = Path(cutpilot_directory or os.environ.get("CUTPILOT_DIRECTORY", "/srv/cutpilot"))
        self.pending: dict[str, tuple[ValidatedPlan, Any, float]] = {}
        self.pending_lock = threading.Lock()

    def _purge_pending(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - self.PENDING_TTL_SECONDS
        for plan_id, (_, _, created_at) in list(self.pending.items()):
            if created_at < cutoff:
                self.pending.pop(plan_id, None)

    def files(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in asdict(item).items() if key != "fingerprint"}
            for item in list_sources(self.ai_cut_directory)
        ]

    def create_plan(self, source: str, task: str) -> dict[str, Any]:
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 4000:
            raise CommandValidationError("Task must contain 1-4000 characters")
        selected = source_metadata(self.ai_cut_directory, source)
        raw = self.ai.create_plan(
            selected.name,
            {"size_bytes": selected.size},
            task.strip(),
        )
        raw = _correct_edit_intent(raw, task.strip())
        raw = _correct_logo_intent(raw, task.strip())
        try:
            plan = validate_plan(selected.name, raw)
        except CommandValidationError:
            logger.exception("AI plan validation failed: source=%s raw=%r", selected.name, raw)
            raise
        plan_id = secrets.token_urlsafe(24)
        with self.pending_lock:
            self._purge_pending()
            if len(self.pending) >= 100:
                self.pending.clear()
            self.pending[plan_id] = (plan, selected, time.monotonic())
        return {"plan_id": plan_id, "source_filename": plan.source_filename, "staged_filename": plan.staged_filename, "commands": list(plan.commands), "summary": plan.summary}

    def confirm(self, plan_id: str, confirmed: bool) -> dict[str, str]:
        if confirmed is not True:
            raise JobError("Explicit confirmation is required")
        if not isinstance(plan_id, str) or not 1 <= len(plan_id) <= 200:
            raise JobError("Invalid plan id")
        with self.pending_lock:
            self._purge_pending()
            item = self.pending.get(plan_id)
            if item is None:
                raise JobError("Plan is missing or has already been used")
            plan, selected, _ = item
            name = handoff(self.ai_cut_directory, self.cutpilot_directory, plan, selected)
            self.pending.pop(plan_id, None)
        return {"status": "queued", "filename": name}


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(service: CutPilotService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CutPilot/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health/live":
                _json_response(self, HTTPStatus.OK, {"status": "ok"})
                return
            if self.path == "/health/ready":
                ai_ready = bool(getattr(service.ai, "api_key", "") and getattr(service.ai, "model", ""))
                files_ready = service.ai_cut_directory.is_dir()
                status = HTTPStatus.OK if ai_ready and files_ready else HTTPStatus.SERVICE_UNAVAILABLE
                _json_response(self, status, {"status": "ok" if status == HTTPStatus.OK else "not_ready", "ai_cut": files_ready, "ai": ai_ready})
                return
            if self.path == "/api/files":
                try:
                    _json_response(self, HTTPStatus.OK, {"files": service.files()})
                except OSError:
                    _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "AI_Cut is not available"})
                return
            if self.path in {"/", "/index.html"}:
                body = (Path(__file__).parent / "static" / "index.html").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/static/app.js":
                body = (Path(__file__).parent / "static" / "app.js").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/api/plan", "/api/jobs"}:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 32_000:
                    raise ValueError("Request is too large")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object expected")
                if self.path == "/api/plan":
                    result = service.create_plan(payload.get("source"), payload.get("task"))
                else:
                    result = service.confirm(payload.get("plan_id"), payload.get("confirmed"))
                _json_response(self, HTTPStatus.OK, result)
            except (CommandValidationError, JobError, AIProviderError, ValueError) as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except OSError:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Local file operation failed"})

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("CUTPILOT_HOST", "127.0.0.1")
    port = int(os.environ.get("CUTPILOT_PORT", "8787"))
    service = CutPilotService()
    server = ThreadingHTTPServer((host, port), make_handler(service))
    print(f"CutPilot listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run()
