"""Small local-only HTTP application for the CutPilot first release."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

from .ai import AIProviderError, OpenRouterAdapter
from .commands import CommandValidationError, ValidatedPlan, validate_edit_duration, validate_plan, validate_source_filename
from .jobs import JobError, handoff, list_sources, source_metadata
from .media import probe_media
from .rules import simple_plan
from .storage import PlanStore

logger = logging.getLogger("cutpilot.server")


def _correct_logo_intent(raw: Any, task: str) -> Any:
    """Remove logos by default; preserve one only for an explicit positive request."""
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return raw
    lowered = task.casefold()
    positive = bool(re.search(r"(?:с|добавь|добавить|оставь|оставить|наложи|наложить|нанеси|поставь|keep|with)\s*(?:лого|логотип)", lowered))
    negative = bool(re.search(r"(?:без|убери|убрать|удали|удалить|remove)\s*(?:лого|логотип)", lowered))
    if positive and not negative:
        commands = [command for command in raw["commands"] if command not in {"-nl", "-nologo"}]
    else:
        commands = [command for command in raw["commands"] if command != "-nl"]
        if "-nologo" not in commands:
            commands.append("-nologo")
    if len(commands) != len(raw["commands"]):
        corrected = dict(raw)
        corrected["commands"] = commands
        logger.warning("Corrected logo intent: task=%r raw=%r corrected=%r", task, raw, corrected)
        return corrected
    return raw


def _correct_edit_intent(raw: Any, task: str) -> Any:
    """Turn multiple AI edit commands into one worker-compatible command."""
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return raw
    commands = raw["commands"]
    edit_indexes = [index for index, command in enumerate(commands) if isinstance(command, str) and command.startswith(("-crp-", "-crp=", "-crp+"))]
    if len(edit_indexes) < 2:
        return raw
    concatenate = bool(re.search(r"(?:склей|склеить|соедини|соединить|объедини|объединить)", task.casefold()))
    prefixes = {commands[index][:5] for index in edit_indexes}
    if "-crp=" in prefixes:
        logger.warning("Cannot combine multiple keep edit commands: task=%r raw=%r", task, raw)
        return raw
    prefix = "-crp+" if concatenate or "-crp+" in prefixes else "-crp-"
    ranges: list[str] = []
    for index in edit_indexes:
        ranges.extend(commands[index][5:].split("+"))
    corrected = dict(raw)
    corrected["commands"] = [command for index, command in enumerate(commands) if index not in edit_indexes]
    corrected["commands"].insert(min(edit_indexes), prefix + "+".join(ranges))
    logger.warning("Corrected edit intent: task=%r raw=%r corrected=%r", task, raw, corrected)
    return corrected


class CutPilotService:
    PENDING_TTL_SECONDS = 30 * 60
    MAX_UPLOAD_BYTES = 200 * 1024 * 1024 * 1024

    def __init__(self, ai: Any | None = None, ai_cut_directory: Path | None = None, cutpilot_directory: Path | None = None):
        self.ai = ai or OpenRouterAdapter()
        self.ai_cut_directory = Path(ai_cut_directory or os.environ.get("CUTPILOT_AI_CUT_DIRECTORY", "/srv/cutpilot/AI_Cut"))
        self.cutpilot_directory = Path(cutpilot_directory or os.environ.get("CUTPILOT_DIRECTORY", "/srv/cutpilot"))
        db_path = Path(os.environ.get("CUTPILOT_DB_PATH", str(self.cutpilot_directory / "cutpilot.db")))
        self.store = PlanStore(db_path)

    def files(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in asdict(item).items() if key != "fingerprint"}
            for item in list_sources(self.ai_cut_directory)
        ]

    def upload(self, filename: str, stream: Any, length: int) -> str:
        filename = validate_source_filename(unquote(filename))
        if length <= 0 or length > self.MAX_UPLOAD_BYTES:
            raise JobError("File size is not allowed")
        self.ai_cut_directory.mkdir(parents=True, exist_ok=True)
        destination = self.ai_cut_directory / filename
        if destination.exists():
            raise JobError("A file with this name already exists in AI_Cut")
        if shutil.disk_usage(self.ai_cut_directory).free < length:
            raise JobError("Not enough free space for upload")
        temporary = self.ai_cut_directory / f".{filename}.part"
        try:
            with temporary.open("xb") as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise JobError("Upload ended before the declared file size")
                    target.write(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise JobError("Could not save uploaded file") from exc
        return filename

    def jobs(self) -> list[dict[str, Any]]:
        history = {item["staged_filename"]: item for item in self.store.list_jobs()}
        progress_directory = Path(os.environ.get("CUTPILOT_PROGRESS_DIR", str(self.cutpilot_directory / ".cutpilot-progress")))
        if not progress_directory.is_dir():
            return list(history.values())
        result = []
        for path in progress_directory.glob("*.progress"):
            try:
                values = {}
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    key, separator, value = line.partition("=")
                    if separator:
                        values[key] = value
                ffmpeg_progress = path.with_name(path.name + ".ffmpeg")
                if ffmpeg_progress.is_file():
                    for line in ffmpeg_progress.read_text(encoding="utf-8", errors="replace").splitlines():
                        key, separator, value = line.partition("=")
                        if separator:
                            values[key] = value
                name = path.name.rsplit(".", 2)[0]
                job_id = path.name.rsplit(".", 2)[1]
                status = values.get("status", "processing")
                message = values.get("message", "")
                self.store.update_job_by_staged(name, status, message)
                progress = ""
                try:
                    duration = float(values.get("duration", "0") or 0)
                    out_time = float(values.get("out_time_ms", "0") or 0)
                except (TypeError, ValueError):
                    duration = out_time = 0
                try:
                    updated_at = int(values.get("updated_at", "0") or 0)
                except (TypeError, ValueError):
                    updated_at = 0
                if duration > 0 and out_time >= 0:
                    progress = str(min(100, max(0, round(out_time / 1_000_000 / duration * 100))))
                result.append({
                    "id": job_id,
                    "source": name,
                    "status": status,
                    "message": message,
                    "updated_at": updated_at,
                    "progress": progress,
                    "out_time_ms": values.get("out_time_ms", ""),
                })
                history.pop(name, None)
            except OSError:
                continue
        result.extend(history.values())
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    def cancel_job(self, job_id: str) -> None:
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
            raise JobError("Invalid job id")
        progress_directory = Path(os.environ.get("CUTPILOT_PROGRESS_DIR", str(self.cutpilot_directory / ".cutpilot-progress")))
        matches = list(progress_directory.glob(f"*.{job_id}.progress"))
        if len(matches) != 1:
            raise JobError("Job is missing or already finished")
        marker = matches[0].with_name(matches[0].name + ".cancel")
        marker.touch(exist_ok=False)

    def create_plan(self, source: str, task: str) -> dict[str, Any]:
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 4000:
            raise CommandValidationError("Task must contain 1-4000 characters")
        logger.info("plan.start source=%r task=%r", source, task.strip())
        selected = source_metadata(self.ai_cut_directory, source)
        normalized_task = task.strip()
        raw = simple_plan(normalized_task) if isinstance(self.ai, OpenRouterAdapter) else None
        if raw is not None:
            logger.info("Using local rule plan: task=%r plan=%r", normalized_task, raw)
        else:
            metadata: dict[str, Any] = {"size_bytes": selected.size}
            try:
                metadata.update(probe_media(self.ai_cut_directory / selected.name))
            except (OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError) as exc:
                logger.warning("Media probe unavailable for %s: %s", selected.name, exc)
            if isinstance(self.ai, OpenRouterAdapter):
                raw = simple_plan(normalized_task, metadata.get("duration_seconds"))
            if raw is None:
                raw = self.ai.create_plan(selected.name, metadata, normalized_task)
        raw = _correct_edit_intent(raw, task.strip())
        raw = _correct_logo_intent(raw, task.strip())
        try:
            plan = validate_plan(selected.name, raw)
        except CommandValidationError:
            logger.exception("AI plan validation failed: source=%s raw=%r", selected.name, raw)
            raise
        if any(command.startswith("-crp") for command in plan.commands):
            try:
                duration = probe_media(self.ai_cut_directory / selected.name).get("duration_seconds")
            except (OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError) as exc:
                raise CommandValidationError("Не удалось проверить длительность видео для таймкодов") from exc
            validate_edit_duration(plan.commands, duration)
        plan_id = secrets.token_urlsafe(24)
        self.store.save(plan_id, plan, selected, self.PENDING_TTL_SECONDS)
        logger.info("plan.ready plan_id=%s source=%r commands=%r staged=%r summary=%r", plan_id, plan.source_filename, plan.commands, plan.staged_filename, plan.summary)
        return {"plan_id": plan_id, "source_filename": plan.source_filename, "staged_filename": plan.staged_filename, "commands": list(plan.commands), "summary": plan.summary}

    def confirm(self, plan_id: str, confirmed: bool) -> dict[str, str]:
        logger.info("job.confirm plan_id=%r confirmed=%r", plan_id, confirmed)
        if confirmed is not True:
            raise JobError("Explicit confirmation is required")
        if not isinstance(plan_id, str) or not 1 <= len(plan_id) <= 200:
            raise JobError("Invalid plan id")
        item = self.store.take(plan_id)
        if item is None:
            raise JobError("Plan is missing or has already been used")
        plan, selected = item
        self.store.create_job(plan_id, plan)
        try:
            name = handoff(self.ai_cut_directory, self.cutpilot_directory, plan, selected)
        except (JobError, OSError) as exc:
            self.store.update_job(plan_id, "failed", str(exc))
            logger.exception("job.handoff_failed plan_id=%s source=%r", plan_id, plan.source_filename)
            raise
        self.store.update_job(plan_id, "queued", name)
        logger.info("job.handoff_ok plan_id=%s source=%r staged=%r", plan_id, plan.source_filename, name)
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
            if self.path == "/api/jobs":
                try:
                    _json_response(self, HTTPStatus.OK, {"jobs": service.jobs()})
                except OSError:
                    _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Queue is not available"})
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
            if self.path == "/static/cutpilot-logo.png":
                body = (Path(__file__).parent / "static" / "cutpilot-logo.png").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            request_id = secrets.token_hex(8)
            logger.info("http.start request_id=%s path=%s", request_id, self.path)
            if self.path == "/api/upload":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    filename = self.headers.get("X-Filename", "")
                    logger.info("upload.start request_id=%s filename=%r bytes=%s", request_id, filename, length)
                    result = {"filename": service.upload(filename, self.rfile, length)}
                    logger.info("upload.ok request_id=%s result=%r", request_id, result)
                    _json_response(self, HTTPStatus.OK, result)
                except (CommandValidationError, JobError, ValueError) as exc:
                    logger.exception("upload.failed request_id=%s error=%s", request_id, exc)
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except OSError:
                    logger.exception("upload.failed request_id=%s local_file_error", request_id)
                    _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Local file operation failed"})
                return
            if self.path not in {"/api/plan", "/api/jobs", "/api/jobs/cancel"}:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 32_000:
                    raise ValueError("Request is too large")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object expected")
                logger.info("http.payload request_id=%s path=%s payload=%r", request_id, self.path, payload)
                if self.path == "/api/plan":
                    result = service.create_plan(payload.get("source"), payload.get("task"))
                elif self.path == "/api/jobs/cancel":
                    service.cancel_job(payload.get("id"))
                    result = {"status": "cancelling"}
                else:
                    result = service.confirm(payload.get("plan_id"), payload.get("confirmed"))
                logger.info("http.ok request_id=%s path=%s result=%r", request_id, self.path, result)
                _json_response(self, HTTPStatus.OK, result)
            except (CommandValidationError, JobError, AIProviderError, ValueError) as exc:
                logger.exception("http.failed request_id=%s path=%s error=%s", request_id, self.path, exc)
                response = {"error": str(exc)}
                if self.path == "/api/plan" and isinstance(exc, (CommandValidationError, AIProviderError)):
                    response = {
                        "error": "Не удалось безопасно подготовить план. Уточните действие, таймкоды или формат видео.",
                        "code": "plan_unclear",
                    }
                _json_response(self, HTTPStatus.BAD_REQUEST, response)
            except OSError:
                logger.exception("http.failed request_id=%s path=%s local_file_error", request_id, self.path)
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
