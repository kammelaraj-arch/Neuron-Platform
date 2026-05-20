"""Persistent crash log.

When the firmware crashes (uncaught exception in app or baseline), we
write the traceback + reset cause + uptime to /crash.log on flash.
On the next boot, baseline reads it and ships it upstream as an
emergency event so the master sees what happened.

This turns "Pico mysteriously reset 3 times last night" into "Pico
hit MemoryError at app/main.py:142 — here's the trace". Critical for
debugging field deployments where you don't have USB access.

Ring-limited to ~64 KB; oldest entries truncate.
"""
import json
import sys
import time


_LOG_PATH = "crash.log"
_MAX_BYTES = 65536


def _now_iso():
    if hasattr(time, "localtime"):
        try:
            t = time.localtime()
            return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(*t[:6])
        except Exception:
            pass
    return str(time.ticks_ms() if hasattr(time, "ticks_ms") else 0)


class CrashLog:
    def __init__(self, path=_LOG_PATH):
        self.path = path

    # ── writing ────────────────────────────────────────────────────
    def record(self, kind: str, info=None, exc=None) -> None:
        """Append a crash entry. kind: "boot_failure" | "uncaught_exception"
        | "watchdog_reset" | "panic"."""
        entry = {
            "ts":   _now_iso(),
            "kind": kind,
            "info": info or {},
        }
        if exc is not None:
            entry["exception"] = self._format_exc(exc)
        try:
            self._append(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _format_exc(self, exc) -> dict:
        try:
            import io
            buf = io.StringIO() if hasattr(__import__("io"), "StringIO") else None
        except Exception:
            buf = None
        out = {"type": type(exc).__name__, "msg": str(exc)}
        try:
            # MicroPython has sys.print_exception(exc, file=)
            if buf is not None and hasattr(sys, "print_exception"):
                sys.print_exception(exc, buf)
                out["trace"] = buf.getvalue()
        except Exception:
            pass
        return out

    def _append(self, line: str) -> None:
        with open(self.path, "a") as f:
            f.write(line)
        # Rotate if oversize.
        try:
            import os
            size = os.stat(self.path)[6]
            if size > _MAX_BYTES:
                self._rotate()
        except Exception:
            pass

    def _rotate(self) -> None:
        try:
            with open(self.path, "r") as f:
                data = f.read()
            keep = data[-int(_MAX_BYTES * 0.5):]
            with open(self.path, "w") as f:
                f.write("# truncated\n")
                f.write(keep)
        except Exception:
            pass

    # ── reading (called by boot to ship up) ─────────────────────────
    def drain(self) -> list:
        """Read all entries and clear the file."""
        out = []
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
            with open(self.path, "w") as f:
                pass
        except OSError:
            pass
        return out
