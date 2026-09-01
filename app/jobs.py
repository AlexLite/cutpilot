"""Safe local file discovery and atomic hand-off to the existing watcher."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import uuid

from .commands import VIDEO_EXTENSIONS, ValidatedPlan, build_worker_output_filename, validate_source_filename


class JobError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFile:
    name: str
    size: int
    modified_ns: int
    changed_ns: int
    fingerprint: str | None = None


def _fingerprint(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def handoff(directory: Path, cutpilot_directory: Path, plan: ValidatedPlan, expected: SourceFile) -> str:
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
    destination = (destination_root / plan.staged_filename).resolve()
    if destination.parent != destination_root:
        raise JobError("Unsafe destination filename")
    if destination.exists():
        raise JobError("A job with this filename already exists in the CutPilot queue")
    result = destination_root / build_worker_output_filename(plan.staged_filename)
    if result.exists():
        raise JobError("The CutPilot result already exists; refusing to overwrite it")

    temporary = destination_root / f".cutpilot.{uuid.uuid4().hex}.part"
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise JobError("Could not atomically hand off the source to CutPilot") from exc
    return destination.name
