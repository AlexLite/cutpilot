"""Small local-only HTTP application for the CutPilot first release."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

from .ai import AIProviderError, OpenRouterAdapter
from .commands import CommandValidationError, ValidatedPlan, build_queue_filename, build_russian_summary, validate_edit_duration, validate_plan, validate_source_filename
from .jobs import JobError, _with_increment, handoff, list_sources, source_metadata
from .learned import LearnedDictionary
from .manifest import write_manifest
from .media import probe_media
from .rules import simple_plan
from .storage import PlanStore

logger = logging.getLogger("cutpilot.server")
_CONFIRM_LOCK = threading.Lock()


class _RateLimiter:
    """Small process-local limiter for the LAN-only HTTP surface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = {}

    def allow(self, client: str, route: str, limit: int) -> bool:
        now = time.monotonic()
        key = (client, route)
        with self._lock:
            hits = [value for value in self._hits.get(key, []) if now - value < 60]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _correct_logo_intent(raw: Any, task: str) -> Any:
    """Remove logos by default; preserve one only for an explicit positive request."""
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return raw
    lowered = task.casefold()
    overlay = bool(re.search(r"(?:добавь|добавить|наложи|наложить|нанеси|нанести|поставь|поставить|приклей|приклеить)\s+(?:лого|логотип|logo)", lowered))
    positive = bool(re.search(r"(?:с|c|оставь|оставить|keep|with)\s*(?:лого|логотип|logo)", lowered))
    negative = bool(re.search(r"(?:без|убери|убрать|удали|удалить|remove)\s*(?:лого|логотип)", lowered))
    if overlay and not negative:
        commands = [command for command in raw["commands"] if command != "-nologo"]
        if "-nl" not in commands:
            commands.append("-nl")
    elif positive and not negative:
        commands = [command for command in raw["commands"] if command not in {"-nl", "-nologo"}]
    else:
        commands = [command for command in raw["commands"] if command != "-nl"]
        if "-nologo" not in commands:
            commands.append("-nologo")
    if commands != raw["commands"]:
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
        dictionary_path = Path(os.environ.get("CUTPILOT_DICTIONARY_PATH", "/var/lib/cutpilot/learned_dictionary.json"))
        self.learned = LearnedDictionary(dictionary_path)
        # Keep manifests outside the shared media directory by default. The
        # LXC unit supplies /var/lib/cutpilot/jobs explicitly; deriving a
        # sibling directory here keeps local tests and developer runs
        # self-contained and writable.
        default_manifest_directory = self.cutpilot_directory.parent / f".{self.cutpilot_directory.name}-jobs"
        self.manifest_directory = Path(os.environ.get("CUTPILOT_JOB_MANIFEST_DIR", str(default_manifest_directory)))

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
        # Each request gets its own staging name.  A shared `.{filename}.part`
        # lets a concurrent failed upload delete another request's tempfile.
        temporary = self.ai_cut_directory / f".upload.{secrets.token_hex(16)}.part"
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
            # link() publishes without overwriting a concurrently-created
            # destination; rename/replace would reintroduce a TOCTOU race.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise JobError("A file with this name already exists in AI_Cut") from exc
            temporary.unlink()
        except (OSError, JobError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
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
        raw = self.learned.lookup(normalized_task) if isinstance(self.ai, OpenRouterAdapter) else None
        used_ai = False
        if raw is not None:
            logger.info("Using learned dictionary plan: task=%r plan=%r", normalized_task, raw)
        else:
            raw = simple_plan(normalized_task) if isinstance(self.ai, OpenRouterAdapter) else None
        if raw is not None:
            logger.info("Using local rule plan: task=%r plan=%r", normalized_task, raw)
            metadata: dict[str, Any] = {"size_bytes": selected.size}
            if any(isinstance(command, str) and command.startswith("-crp") for command in raw.get("commands", [])):
                try:
                    metadata.update(probe_media(self.ai_cut_directory / selected.name))
                except (OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError) as exc:
                    logger.warning("Media probe unavailable for %s: %s", selected.name, exc)
        else:
            metadata: dict[str, Any] = {"size_bytes": selected.size}
            try:
                metadata.update(probe_media(self.ai_cut_directory / selected.name))
            except (OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError) as exc:
                logger.warning("Media probe unavailable for %s: %s", selected.name, exc)
            if isinstance(self.ai, OpenRouterAdapter):
                raw = simple_plan(normalized_task, metadata.get("duration_seconds"))
            if raw is None:
                used_ai = True
                raw = self.ai.create_plan(selected.name, metadata, normalized_task)
        max_attempts = max(1, min(10, int(os.environ.get("CUTPILOT_AI_MAX_ATTEMPTS", "3"))))
        seen_plans: set[str] = set()
        for attempt in range(1, max_attempts + 1):
            corrected = _correct_edit_intent(raw, task.strip())
            corrected = _correct_logo_intent(corrected, task.strip())
            fingerprint = repr(corrected)
            try:
                plan = validate_plan(selected.name, corrected)
                if any(command.startswith("-crp") for command in plan.commands):
                    duration = metadata.get("duration_seconds")
                    if duration is None:
                        duration = probe_media(self.ai_cut_directory / selected.name).get("duration_seconds")
                    validate_edit_duration(plan.commands, duration)
                break
            except (CommandValidationError, OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError) as exc:
                if not used_ai or attempt >= max_attempts or fingerprint in seen_plans:
                    logger.exception("AI plan validation failed: source=%s attempt=%s raw=%r", selected.name, attempt, corrected)
                    if isinstance(exc, (OSError, StopIteration, TypeError, ValueError, subprocess.SubprocessError)):
                        raise CommandValidationError("Не удалось проверить план и длительность видео") from exc
                    raise
                seen_plans.add(fingerprint)
                logger.warning("AI plan rejected; retrying: source=%s attempt=%s/%s error=%s", selected.name, attempt, max_attempts, exc)
                raw = self.ai.create_plan(selected.name, metadata, normalized_task, feedback=str(exc))
        else:
            raise CommandValidationError("Не удалось подготовить план")
        plan_id = secrets.token_urlsafe(24)
        # The provider summary is advisory.  Build the visible description
        # from the validated commands so the plan is always Russian and
        # cannot claim an operation different from the one being queued.
        plan = replace(plan, task=normalized_task, summary=build_russian_summary(plan.source_filename, plan.commands))
        self.store.save(plan_id, plan, selected, self.PENDING_TTL_SECONDS)
        logger.info("plan.ready plan_id=%s source=%r commands=%r staged=%r summary=%r", plan_id, plan.source_filename, plan.commands, plan.staged_filename, plan.summary)
        return {"plan_id": plan_id, "source_filename": plan.source_filename, "staged_filename": build_queue_filename(plan.source_filename, plan.commands), "commands": list(plan.commands), "summary": plan.summary}

    def confirm(self, plan_id: str, confirmed: bool) -> dict[str, str]:
        logger.info("job.confirm plan_id=%r confirmed=%r", plan_id, confirmed)
        if confirmed is not True:
            raise JobError("Explicit confirmation is required")
        if not isinstance(plan_id, str) or not 1 <= len(plan_id) <= 200:
            raise JobError("Invalid plan id")
        # The HTTP server is threaded. Serialize the consume-and-handoff
        # critical section so two confirmations cannot select the same free
        # queue/result name at the same time.
        with _CONFIRM_LOCK:
            item = self.store.take(plan_id)
            if item is None:
                raise JobError("Plan is missing or has already been used")
            plan, selected = item
            self.cutpilot_directory.mkdir(parents=True, exist_ok=True)
            reserved = False
            for number in range(1000):
                candidate = build_queue_filename(plan.source_filename, plan.commands)
                if number:
                    candidate = _with_increment(candidate, number)
                suffix = "_nologo" if any(command in {"-nl", "-nologo"} for command in plan.commands) else "_logo"
                result = self.cutpilot_directory / f"{Path(candidate).stem}{suffix}{Path(candidate).suffix}"
                if (self.cutpilot_directory / candidate).exists() or result.exists():
                    continue
                candidate_plan = replace(plan, staged_filename=candidate)
                if self.store.create_job(plan_id, candidate_plan):
                    plan = candidate_plan
                    reserved = True
                    break
            if not reserved:
                raise JobError("Could not reserve a free result filename")
            manifest = None
            try:
                manifest = write_manifest(self.manifest_directory, plan_id, plan.staged_filename, plan.commands + ("-nocut",))
                name = handoff(self.ai_cut_directory, self.cutpilot_directory, plan, selected, allow_increment=False)
            except (JobError, OSError) as exc:
                self.store.update_job(plan_id, "failed", str(exc))
                if manifest is not None:
                    manifest.unlink(missing_ok=True)
                logger.exception("job.handoff_failed plan_id=%s source=%r", plan_id, plan.source_filename)
                raise
            self.store.update_job(plan_id, "queued", name)
            if isinstance(self.ai, OpenRouterAdapter):
                status = self.learned.record(plan.task, plan.commands, plan.summary)
                logger.info("dictionary.record task=%r commands=%r status=%s", plan.task, plan.commands, status)
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
    limiter = _RateLimiter()

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
            if self.path == "/api/jobs/stream":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                previous = None
                try:
                    for _ in range(30):
                        payload = json.dumps({"jobs": service.jobs()}, ensure_ascii=False, separators=(",", ":"))
                        if payload != previous:
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            previous = payload
                        time.sleep(2)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
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
            limits = {
                "/api/plan": int(os.environ.get("CUTPILOT_RATE_LIMIT_PLAN", "10")),
                "/api/upload": int(os.environ.get("CUTPILOT_RATE_LIMIT_UPLOAD", "6")),
                "/api/jobs": int(os.environ.get("CUTPILOT_RATE_LIMIT_CONFIRM", "30")),
                "/api/jobs/cancel": int(os.environ.get("CUTPILOT_RATE_LIMIT_CANCEL", "30")),
            }
            if self.path in limits and not limiter.allow(self.client_address[0], self.path, max(1, limits[self.path])):
                _json_response(self, HTTPStatus.TOO_MANY_REQUESTS, {"error": "Слишком много запросов. Повторите позже."})
                return
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
