"""Strict, non-shell validation for the CutPilot filename command language."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path


VIDEO_EXTENSIONS = {"mp4", "mkv", "mov", "avi", "m4v", "webm", "ts", "m2ts", "mts"}
_TIME = r"[0-9]{1,2}\.[0-5][0-9](?:\.[0-5][0-9])?"
_EDIT = re.compile(rf"^(?P<start>{_TIME})-(?P<end>{_TIME})(?P<rest>.*)$")
_COMMAND_STEM = re.compile(r"\s*\[cmd(?:\s|-).*\]\s*$", re.IGNORECASE)


class CommandValidationError(ValueError):
    """Raised when an AI plan cannot be represented by CutPilot safely."""


@dataclass(frozen=True)
class ValidatedPlan:
    source_filename: str
    commands: tuple[str, ...]
    summary: str
    staged_filename: str
    task: str = ""


def _time_to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(".")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _validate_edit(token: str) -> None:
    if token.startswith("-crp-"):
        mode, rest = "remove", token[5:]
    elif token.startswith("-crp="):
        mode, rest = "keep", token[5:]
    elif token.startswith("-crp+"):
        mode, rest = "concat", token[5:]
    else:
        raise CommandValidationError(f"Unsupported edit command: {token}")

    ranges: list[tuple[int, int]] = []
    while rest:
        match = _EDIT.match(rest)
        if not match:
            raise CommandValidationError(
                f"Invalid edit range: {token}; use MM.SS-MM.SS, for example -crp-00.00-00.10"
            )
        start = _time_to_seconds(match.group("start"))
        end = _time_to_seconds(match.group("end"))
        if end <= start:
            raise CommandValidationError("Each edit range must end after its start")
        ranges.append((start, end))
        rest = match.group("rest")
        if rest.startswith("+"):
            if mode == "keep":
                raise CommandValidationError("crp= accepts one range only")
            rest = rest[1:]
        elif rest:
            raise CommandValidationError(f"Invalid edit range: {token}")

    if mode == "remove":
        previous_end = -1
        for start, end in ranges:
            if start < previous_end:
                raise CommandValidationError("Removed ranges must be chronological and non-overlapping")
            previous_end = end


def _normalize_edit_token(token: object) -> object:
    """Normalize AI edit timestamps, including minute values above 99."""
    if not isinstance(token, str):
        return token
    prefix = next((value for value in ("-crp-", "-crp=", "-crp+") if token.startswith(value)), None)
    if prefix is None:
        return token
    ranges = token[len(prefix):].split("+")
    normalized: list[str] = []
    for item in ranges:
        match = re.fullmatch(r"([0-9]+)-([0-9]+)", item)
        def timestamp(value: int) -> str:
            hours, remainder = divmod(value, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}.{minutes:02d}.{seconds:02d}" if hours else f"{minutes:02d}.{seconds:02d}"
        if match:
            start, end = (int(value) for value in match.groups())
            normalized.append(f"{timestamp(start)}-{timestamp(end)}")
            continue
        formatted = re.fullmatch(r"([0-9]+(?:\.[0-9]{1,2}){1,2})-([0-9]+(?:\.[0-9]{1,2}){1,2})", item)
        if not formatted:
            return token
        def parse(value: str) -> int:
            parts = [int(part) for part in value.split(".")]
            if len(parts) == 2:
                minutes, seconds = parts
                if seconds > 59:
                    raise ValueError
                return minutes * 60 + seconds
            hours, minutes, seconds = parts
            if minutes > 59 or seconds > 59:
                raise ValueError
            return hours * 3600 + minutes * 60 + seconds
        try:
            start, end = parse(formatted.group(1)), parse(formatted.group(2))
        except ValueError:
            return token
        normalized.append(f"{timestamp(start)}-{timestamp(end)}")
    return prefix + "+".join(normalized)


def validate_commands(commands: object) -> tuple[str, ...]:
    """Validate only commands understood by the CutPilot worker."""

    if not isinstance(commands, list) or len(commands) > 20:
        raise CommandValidationError("commands must be an array containing at most 20 items")

    seen: set[str] = set()
    container: str | None = None
    edit_seen = False

    for command in commands:
        if not isinstance(command, str) or not command or len(command) > 160 or any(ch.isspace() for ch in command):
            raise CommandValidationError("Each command must be a short token without whitespace")
        if command in seen:
            raise CommandValidationError(f"Duplicate command: {command}")
        seen.add(command)

        if command.startswith("-crp"):
            if edit_seen:
                raise CommandValidationError("Only one edit command is allowed")
            _validate_edit(command)
            edit_seen = True
        elif command in {"-nl", "-nologo", "-nc", "-nocut", "-hevc"}:
            pass
        elif command in {"-mp4", "-mov"}:
            if container is not None:
                raise CommandValidationError("Only one output container is allowed")
            container = command[1:]
        elif re.fullmatch(r"-(?:1080|720|480|360)p", command):
            pass
        elif re.fullmatch(r"-[1-9][0-9]?fps", command):
            fps = int(command[1:-3])
            if not 1 <= fps <= 60:
                raise CommandValidationError("FPS must be between 1 and 60")
        elif re.fullmatch(r"-[1-9][0-9]*(?:gb|mb)", command):
            number = int(command[1:-2])
            unit = command[-2:]
            size_bytes = number * (1_000_000_000 if unit == "gb" else 1_000_000)
            if not 1_000_000 <= size_bytes <= 2_000_000_000_000:
                raise CommandValidationError("Target size must be between 1mb and 2000gb")
            pass
        else:
            raise CommandValidationError(f"Unknown CutPilot command: {command}")

    return tuple(commands)


def validate_edit_duration(commands: tuple[str, ...], duration_seconds: float | str) -> None:
    """Reject edit ranges that extend beyond the actual media duration."""
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise CommandValidationError("Video duration is unavailable") from exc
    if duration <= 0:
        raise CommandValidationError("Video duration is invalid")
    for command in commands:
        if not command.startswith("-crp"):
            continue
        rest = command[5:]
        while rest:
            match = _EDIT.match(rest)
            if match is None:
                return
            end = _time_to_seconds(match.group("end"))
            if end > duration:
                raise CommandValidationError(
                    f"Edit range {match.group('start')}-{match.group('end')} is outside video duration ({duration:.2f}s)"
                )
            rest = match.group("rest")
            if rest.startswith("+"):
                rest = rest[1:]
            elif rest:
                return


def validate_source_filename(filename: object) -> str:
    if not isinstance(filename, str) or not filename or len(filename) > 240:
        raise CommandValidationError("Invalid source filename")
    path = Path(filename)
    if path.name != filename or "/" in filename or "\\" in filename or filename in {".", ".."} or "\x00" in filename:
        raise CommandValidationError("Source must be a direct child filename")
    if path.suffix.lower().lstrip(".") not in VIDEO_EXTENSIONS:
        raise CommandValidationError("Unsupported video extension")
    if _COMMAND_STEM.search(path.stem):
        raise CommandValidationError("Source filename already contains a CutPilot command block")
    if "_logo" in path.stem.casefold() or "_nologo" in path.stem.casefold() or ".tmp." in path.stem.casefold():
        raise CommandValidationError("Source filename is reserved by the CutPilot worker")
    return filename


def build_staged_filename(source_filename: str, commands: tuple[str, ...]) -> str:
    source = Path(source_filename)
    stem = source.stem
    extension = source.suffix.lower().lstrip(".")
    if "-mp4" in commands:
        extension = "mp4"
    elif "-mov" in commands:
        extension = "mov"
    elif "-hevc" in commands and not ("-mp4" in commands or "-mov" in commands):
        extension = "mp4"
    command_block = f" [cmd {' '.join(commands)}]" if commands else ""
    # This is the name consumed by CutPilot, before its result suffix is added.
    result = f"{stem}{command_block}.{extension}"
    if len(result) > 240:
        raise CommandValidationError("Result filename is too long")
    return result


def build_queue_filename(source_filename: str, commands: tuple[str, ...]) -> str:
    """Clean queue name used by structured jobs; commands live in a manifest."""
    source = Path(source_filename)
    extension = source.suffix.lower().lstrip(".")
    if "-mp4" in commands:
        extension = "mp4"
    elif "-mov" in commands:
        extension = "mov"
    elif "-hevc" in commands:
        extension = "mp4"
    return f"{source.stem}.{extension}"


def build_worker_output_filename(staged_filename: str) -> str:
    """Build the final name that the CutPilot watcher will create."""

    staged = Path(staged_filename)
    stem = staged.stem
    command_match = _COMMAND_STEM.search(stem)
    if command_match:
        clean_stem = stem[: command_match.start()].rstrip()
        command_text = command_match.group(0)
        no_logo = "-nl" in command_text.casefold() or "-nologo" in command_text.casefold()
    else:
        clean_stem = stem
        no_logo = False
    suffix = "_nologo" if no_logo else "_logo"
    return f"{clean_stem}{suffix}{staged.suffix}"


def build_russian_summary(source_filename: str, commands: tuple[str, ...]) -> str:
    """Build the user-facing plan description from validated commands.

    The provider's summary is intentionally not shown as-is: providers may
    answer in English or describe commands incorrectly.  The command list has
    already passed CutPilot validation, so it is the reliable source for this
    short Russian description.
    """
    parts: list[str] = []
    for command in commands:
        if command.startswith("-crp+"):
            ranges = command[5:].replace("+", " и ")
            parts.append(f"склеить фрагменты {ranges}")
        elif command.startswith("-crp="):
            parts.append(f"оставить фрагмент {command[5:]}")
        elif command.startswith("-crp-"):
            ranges = command[5:].replace("+", " и ")
            parts.append(f"вырезать фрагменты {ranges}")
        elif command == "-mp4":
            parts.append("пересчитать в MP4")
        elif command == "-mov":
            parts.append("пересчитать в MOV")
        elif command == "-hevc":
            parts.append("использовать кодек HEVC")
        elif command in {"-nl"}:
            parts.append("наложить логотип")
        elif command == "-nologo":
            parts.append("убрать логотип")
        elif command == "-nc":
            parts.append("не обрезать видео по тишине")
        elif command.endswith("p") and command[1:-1].isdigit():
            parts.append(f"установить разрешение {command[1:]}")
        elif command.endswith("fps") and command[1:-3].isdigit():
            parts.append(f"установить частоту {command[1:-3]} кадров/с")
        elif command.endswith("mb") and command[1:-2].isdigit():
            parts.append(f"сжать до {command[1:-2]} МБ")
        elif command.endswith("gb") and command[1:-2].isdigit():
            parts.append(f"сжать до {command[1:-2]} ГБ")
    if not parts:
        return f"Обработать файл «{source_filename}»"
    return f"Для файла «{source_filename}»: " + ", ".join(parts) + "."


def validate_plan(source_filename: object, raw_plan: object) -> ValidatedPlan:
    if not isinstance(raw_plan, dict):
        raise CommandValidationError("AI plan must be a JSON object")
    allowed_keys = {"source_filename", "commands", "summary"}
    if set(raw_plan) - allowed_keys:
        raise CommandValidationError("AI plan contains unsupported fields")
    source = validate_source_filename(source_filename)
    declared_source = raw_plan.get("source_filename", source)
    if declared_source != source:
        raise CommandValidationError("AI plan source filename does not match the selected file")
    raw_commands = raw_plan.get("commands", [])
    if isinstance(raw_commands, list):
        raw_commands = [_normalize_edit_token(command) for command in raw_commands]
    commands = validate_commands(raw_commands)
    summary = raw_plan.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 160:
        raise CommandValidationError("AI plan summary is invalid")
    return ValidatedPlan(source, commands, summary.strip(), build_staged_filename(source, commands))
