"""Admin-only diagnostic endpoints — runtime logs + last unhandled error.

Purpose: give an authorised operator (or Claude, with an admin API key)
a way to fetch the master container's recent log output and the most
recent Python traceback over HTTPS, without needing shell access.

Auth: require_scopes("admin"). Output is regex-scrubbed of obvious
secret patterns (API-key prefixes, Bearer tokens, password= forms,
Fernet ciphertext) before being returned. The endpoints read from
the rotating-file handler installed by main.py at startup.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, User
from ..security.audit import record
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/admin", tags=["admin-diag"])


LOG_DIR = Path("data/logs")
MASTER_LOG = LOG_DIR / "master.log"
ERROR_LOG = LOG_DIR / "errors.log"


_SECRET_PATTERNS = [
    (re.compile(r"sk_[A-Za-z0-9_\-]{16,}"), "sk_<redacted>"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"(?i)(password=)[^&\s\"']+"), r"\1<redacted>"),
    (re.compile(r"(?i)(api[_-]?key[\"':=\s]+)[A-Za-z0-9_\-]{8,}"),
     r"\1<redacted>"),
    (re.compile(r"gAAAAA[A-Za-z0-9_\-=]{40,}"), "<fernet-redacted>"),
]


def _scrub(text: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _tail_lines(path: Path, n: int, block: int = 4096) -> list[str]:
    """Read the last n lines from a file without loading the whole thing."""
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            read_size = min(block, size)
            size -= read_size
            f.seek(size)
            data = f.read(read_size) + data
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    lines = text.splitlines()
    return lines[-n:]


@router.get("/logs", response_class=PlainTextResponse)
async def get_logs(
    lines: int = Query(200, ge=1, le=5000),
    grep: str | None = Query(None, max_length=200),
    actor: APIKey = Depends(require_scopes("admin")),
) -> str:
    """Tail the master log. Optional case-insensitive substring filter."""
    rows = _tail_lines(MASTER_LOG, lines)
    if grep:
        gl = grep.lower()
        rows = [r for r in rows if gl in r.lower()]
    return _scrub("\n".join(rows)) + "\n"


@router.get("/last-error", response_class=PlainTextResponse)
async def get_last_error(
    actor: APIKey = Depends(require_scopes("admin")),
) -> str:
    """Return the most recent traceback block from errors.log."""
    if not ERROR_LOG.exists():
        return "no errors recorded yet\n"
    rows = _tail_lines(ERROR_LOG, 2000)
    # Find the last "=== " separator we write per-error and return
    # everything from there to EOF.
    last_sep = -1
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].startswith("=== "):
            last_sep = i
            break
    if last_sep < 0:
        return _scrub("\n".join(rows)) + "\n"
    return _scrub("\n".join(rows[last_sep:])) + "\n"


@router.get("/errors", response_class=PlainTextResponse)
async def get_errors(
    lines: int = Query(2000, ge=1, le=20000),
    actor: APIKey = Depends(require_scopes("admin")),
) -> str:
    """Tail the full errors log (every captured traceback)."""
    rows = _tail_lines(ERROR_LOG, lines)
    return _scrub("\n".join(rows)) + "\n"


@router.get("/diag/info")
async def get_diag_info(
    actor: APIKey = Depends(require_scopes("admin")),
) -> dict[str, Any]:
    """Lightweight snapshot — log file sizes, mtimes, paths."""
    out: dict[str, Any] = {"log_dir": str(LOG_DIR.resolve())}
    for label, p in (("master_log", MASTER_LOG), ("error_log", ERROR_LOG)):
        if p.exists():
            st = p.stat()
            out[label] = {"path": str(p), "size": st.st_size, "mtime": st.st_mtime}
        else:
            out[label] = {"path": str(p), "size": 0, "exists": False}
    return out


@router.post("/users/{username}/password")
async def admin_set_user_password(
    username: str,
    password: str = Form(..., min_length=8, max_length=200),
    must_change: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("admin")),
) -> dict[str, Any]:
    """Set a user's password directly. Admin-only escape hatch for
    out-of-band resets (lost bootstrap password, etc.). Audit logs
    the action without recording the password material."""
    from argon2 import PasswordHasher
    user = (await session.execute(
        select(User).where(User.username == username)
    )).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, f"user '{username}' not found")
    user.password_hash = PasswordHasher().hash(password)
    user.must_change_password = bool(must_change)
    await record(
        session, actor=actor.id, actor_kind="api_key",
        action="user.admin_set_password", target_kind="user",
        target_id=user.id,
        detail={"username": user.username, "must_change": bool(must_change)},
    )
    await session.commit()
    return {
        "ok": True,
        "username": user.username,
        "must_change_password": user.must_change_password,
    }
