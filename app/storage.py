"""SQLite store for pending plans and durable job history."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from .commands import ValidatedPlan
from .jobs import SourceFile


class PlanStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_modified_ns INTEGER NOT NULL,
                    source_changed_ns INTEGER NOT NULL,
                    source_fingerprint TEXT,
                    commands_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    staged_filename TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    staged_filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save(self, plan_id: str, plan: ValidatedPlan, source: SourceFile, ttl_seconds: int) -> None:
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO plans
                   (id, source, source_size, source_modified_ns, source_changed_ns,
                    source_fingerprint, commands_json, summary, staged_filename,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    source.name,
                    source.size,
                    source.modified_ns,
                    source.changed_ns,
                    source.fingerprint,
                    json.dumps(list(plan.commands), ensure_ascii=False),
                    plan.summary,
                    plan.staged_filename,
                    now,
                    now + ttl_seconds,
                ),
            )

    def take(self, plan_id: str) -> tuple[ValidatedPlan, SourceFile] | None:
        now = time.time()
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM plans WHERE id = ? AND expires_at > ?", (plan_id, now)).fetchone()
            db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        if row is None:
            return None
        plan = ValidatedPlan(
            source_filename=row["source"],
            commands=tuple(json.loads(row["commands_json"])),
            summary=row["summary"],
            staged_filename=row["staged_filename"],
        )
        source = SourceFile(
            name=row["source"],
            size=row["source_size"],
            modified_ns=row["source_modified_ns"],
            changed_ns=row["source_changed_ns"],
            fingerprint=row["source_fingerprint"],
        )
        return plan, source

    def create_job(self, job_id: str, plan: ValidatedPlan) -> None:
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO jobs (id, source, staged_filename, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, plan.source_filename, plan.staged_filename, "queued", now, now),
            )

    def update_job(self, job_id: str, status: str, message: str = "") -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE jobs SET status = ?, message = ?, updated_at = ? WHERE id = ?", (status, message, time.time(), job_id))

    def update_job_by_staged(self, staged_filename: str, status: str, message: str = "") -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE jobs SET status = ?, message = ?, updated_at = ? WHERE staged_filename = ? AND (status != ? OR message != ?)",
                (status, message, time.time(), staged_filename, status, message),
            )

    def list_jobs(self) -> list[dict[str, object]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id, source, staged_filename, status, message, created_at, updated_at FROM jobs ORDER BY updated_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]
