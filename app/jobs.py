"""Safe local file discovery and atomic hand-off to the existing watcher."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid

from .commands import VIDEO_EXTENSIONS, ValidatedPlan, build_worker_output_filename, validate_source_filename


class JobError(RuntimeError):
    pass


_HANDOFF_LOCK = threading.Lock()


@dataclass(frozen=True)
class SourceFile:
    name: str
    size: int
    modified_ns: int
    changed_ns: int
    fingerprint: str | None = None


def _with_increment(filename: str, number: int) -> str:
    path = Path(filename)
    stem = path.stem
    command_marker = re.search(r"\s+\[cmd(?:\s|-).*$", stem, re.IGNORECASE)
    if command_marker:
        stem = f"{stem[:command_marker.start()].rstrip()}_{number}{stem[command_marker.start():]}"
    else:
        stem = f"{stem}_{number}"
    return f"{stem}{path.suffix}"


def with_no_auto_tail(filename: str) -> str:
    """Mark API handoffs so the watcher skips implicit tail trimming."""
    path = Path(filename)
    marker = re.search(r"\s+\[cmd.*\](?=\.[^.]+$)", path.name, re.IGNORECASE)
    if marker:
        return f"{path.name[:marker.end() - 1]} -nocut]{path.suffix}"
    return f"{path.stem} [cmd -nocut]{path.suffix}"


def _fingerprint(path: Path) -> str:
    """Hash a small stable sample instead of reading the whole video."""
    digest = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    with path.open("rb") as handle:
        sample_size = min(4 * 1024 * 1024, size)
        digest.update(size.to_bytes(8, "little"))
        digest.update(handle.read(sample_size))
        if size > sample_size:
            handle.seek(max(0, size - sample_size))
            digest.update(handle.read(sample_size))
        return digest.hexdigest()


def _copy_fast(source: Path, temporary: Path) -> None:
    """Copy a stable snapshot; a hardlink would track later source mutations."""
    try:
        subprocess.run(
            ["cp", "--reflink=auto", "--", str(source), str(temporary)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except (OSError, subprocess.CalledProcessError):
        pass
    with source.open("rb") as input_file, temporary.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())


def _direct_file(directory: Path, filename: str) -> Path:
    validate_source_filename(filename)
    directory = directory.resolve()
    raw_candidate = directory / filename
    if raw_candidate.is_symlink():
        raise JobError("Source file must not be a symlink")
    candidate = raw_candidate.resolve()
    if candidate.parent != directory or not candidate.is_file():
        raise JobError("Source file is not available in AI_Cut")
    return candidate


def list_sources(directory: Path) -> list[SourceFile]:
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for item in directory.iterdir():
        if item.is_file() and not item.is_symlink() and item.suffix.lower().lstrip(".") in VIDEO_EXTENSIONS:
            try:
                validate_source_filename(item.name)
            except ValueError:
                continue
            stat = item.stat()
            result.append(SourceFile(item.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return sorted(result, key=lambda item: item.name.casefold())


def source_metadata(directory: Path, filename: str) -> SourceFile:
    path = _direct_file(directory, filename)
    stat = path.stat()
    return SourceFile(path.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, _fingerprint(path))


def handoff(directory: Path, cutpilot_directory: Path, plan: ValidatedPlan, expected: SourceFile, *, allow_increment: bool = True) -> str:
    source = _direct_file(directory, plan.source_filename)
    current = source.stat()
    if (
        current.st_size != expected.size
        or current.st_mtime_ns != expected.modified_ns
        or current.st_ctime_ns != expected.changed_ns
        or (expected.fingerprint is not None and _fingerprint(source) != expected.fingerprint)
    ):
        raise JobError("Source changed after planning; generate a new plan")

    destination_root = cutpilot_directory.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    with _HANDOFF_LOCK:
        for number in range(1000):
            if number and not allow_increment:
                break
            candidate = plan.staged_filename if number == 0 else _with_increment(plan.staged_filename, number)
            destination = (destination_root / candidate).resolve()
            if destination.parent != destination_root:
                raise JobError("Unsafe destination filename")
            suffix = "_nologo" if any(command in {"-nl", "-nologo"} for command in plan.commands) else "_logo"
            result = destination_root / f"{Path(candidate).stem}{suffix}{Path(candidate).suffix}"
            if destination.exists() or result.exists():
                continue
            temporary = destination_root / f".cutpilot.{uuid.uuid4().hex}.part"
            try:
                _copy_fast(source, temporary)
                # Hard-linking the completed temp file is an atomic no-clobber
                # publish on the local filesystem.  Unlike os.replace it can
                # never overwrite a queue file created concurrently.
                os.link(temporary, destination)
                temporary.unlink()
                return destination.name
            except FileExistsError:
                temporary.unlink(missing_ok=True)
                continue
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise JobError("Could not atomically hand off the source to CutPilot") from exc
    raise JobError("Could not find a free result filename")
