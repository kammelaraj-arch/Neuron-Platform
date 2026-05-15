"""DBHelper — SQLite storage for plot profiles + their precomputed step
sequences. Single-file DB at /opt/sjweb/sjweb.db on the device."""
from __future__ import annotations

import os
import sqlite3
import threading

_DB_PATH = os.environ.get("SJWEB_DB", "/opt/sjweb/sjweb.db")


class DBHelper:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or _DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    scale REAL NOT NULL DEFAULT 1.0,
                    smoothing INTEGER NOT NULL DEFAULT 3,
                    step_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS profile_steps (
                    profile_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    x_step INTEGER NOT NULL,
                    y_step INTEGER NOT NULL,
                    x_dir INTEGER NOT NULL,
                    y_dir INTEGER NOT NULL,
                    PRIMARY KEY (profile_id, seq),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                        ON DELETE CASCADE
                )
            """)

    def list_profiles(self):
        with self._lock, self._conn() as c:
            return c.execute(
                "SELECT id, name, scale, smoothing, step_count FROM profiles "
                "ORDER BY created_at DESC"
            ).fetchall()

    def get_profile(self, profile_id: int):
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT id, name, scale, smoothing, step_count FROM profiles "
                "WHERE id = ?",
                (profile_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0], "name": row[1], "scale": row[2],
                "smoothing": row[3], "step_count": row[4],
            }

    def get_profile_steps(self, profile_id: int):
        with self._lock, self._conn() as c:
            return c.execute(
                "SELECT seq, x_step, y_step, x_dir, y_dir FROM profile_steps "
                "WHERE profile_id = ? ORDER BY seq",
                (profile_id,),
            ).fetchall()

    def insert_profile(self, name: str, scale: float, smoothing: int, steps: list) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO profiles (name, scale, smoothing, step_count) "
                "VALUES (?, ?, ?, ?)",
                (name, scale, smoothing, len(steps)),
            )
            pid = cur.lastrowid
            c.executemany(
                "INSERT INTO profile_steps (profile_id, seq, x_step, y_step, x_dir, y_dir) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(pid, i, x_step, y_step, x_dir, y_dir)
                 for i, (x_step, y_step, x_dir, y_dir) in enumerate(steps)],
            )
            return pid

    def delete_profile(self, profile_id: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM profile_steps WHERE profile_id = ?", (profile_id,))
            c.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
