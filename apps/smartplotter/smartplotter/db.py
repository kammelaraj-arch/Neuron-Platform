"""SQLite profile + run-log store. Single-file DB at
/opt/smartplotter/state.db on a deployed device.

Schemas mirror sjweb's so existing profiles port cleanly; runs is new
and is the audit trail (every profile execution captured with a
start/end timestamp + reason if aborted)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_DEFAULT = "/opt/smartplotter/state.db"


class ProfileStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("SMARTPLOTTER_DB", _DEFAULT)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _c(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def _init(self) -> None:
        with self._lock, self._c() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    contour_json TEXT NOT NULL,
                    feed_mm_s REAL NOT NULL DEFAULT 30,
                    z_lift_mm REAL NOT NULL DEFAULT 2,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER,
                    started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    points_completed INTEGER NOT NULL DEFAULT 0,
                    points_total INTEGER NOT NULL DEFAULT 0,
                    fault TEXT,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
                )
            """)

    # ── Profile CRUD ──────────────────────────────────────────────────
    def list_profiles(self) -> list[dict]:
        with self._lock, self._c() as c:
            rows = c.execute(
                "SELECT id, name, feed_mm_s, z_lift_mm, created_at FROM profiles "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [{"id": r[0], "name": r[1], "feed_mm_s": r[2],
                 "z_lift_mm": r[3], "created_at": r[4]} for r in rows]

    def get_profile(self, pid: int) -> dict | None:
        with self._lock, self._c() as c:
            row = c.execute(
                "SELECT id, name, contour_json, feed_mm_s, z_lift_mm, notes "
                "FROM profiles WHERE id = ?",
                (pid,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1],
                "contours": json.loads(row[2]),
                "feed_mm_s": row[3], "z_lift_mm": row[4], "notes": row[5]}

    def insert_profile(self, name: str, contours: list[list[tuple[float, float]]],
                       feed_mm_s: float, z_lift_mm: float, notes: str = "") -> int:
        with self._lock, self._c() as c:
            cur = c.execute(
                "INSERT INTO profiles (name, contour_json, feed_mm_s, z_lift_mm, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, json.dumps(contours), feed_mm_s, z_lift_mm, notes),
            )
            return cur.lastrowid

    def delete_profile(self, pid: int) -> None:
        with self._lock, self._c() as c:
            c.execute("DELETE FROM profiles WHERE id = ?", (pid,))

    # ── Run audit log ─────────────────────────────────────────────────
    def start_run(self, pid: int, total: int) -> int:
        with self._lock, self._c() as c:
            cur = c.execute(
                "INSERT INTO runs (profile_id, points_total) VALUES (?, ?)",
                (pid, total),
            )
            return cur.lastrowid

    def update_run(self, run_id: int, completed: int) -> None:
        with self._lock, self._c() as c:
            c.execute("UPDATE runs SET points_completed = ? WHERE id = ?",
                      (completed, run_id))

    def end_run(self, run_id: int, status: str = "ok", fault: str | None = None) -> None:
        with self._lock, self._c() as c:
            c.execute(
                "UPDATE runs SET ended_at = datetime('now'), status = ?, fault = ? "
                "WHERE id = ?",
                (status, fault, run_id),
            )

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._lock, self._c() as c:
            rows = c.execute(
                "SELECT id, profile_id, started_at, ended_at, status, "
                "points_completed, points_total, fault FROM runs "
                "ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"id": r[0], "profile_id": r[1], "started_at": r[2],
                 "ended_at": r[3], "status": r[4],
                 "points_completed": r[5], "points_total": r[6],
                 "fault": r[7]} for r in rows]
