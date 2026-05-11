"""Tiny JSON-line logger for MicroPython. Stays under 8 KiB so it runs on Pico."""
import json
import time

_LEVELS = ("debug", "info", "warning", "error", "critical")
_MIN_LEVEL = "info"


def set_level(level):
    global _MIN_LEVEL
    if level in _LEVELS:
        _MIN_LEVEL = level


def _enabled(level):
    try:
        return _LEVELS.index(level) >= _LEVELS.index(_MIN_LEVEL)
    except ValueError:
        return True


def _emit(level, source, message, **kwargs):
    if not _enabled(level):
        return
    rec = {"t": time.time(), "lvl": level, "src": source, "msg": message}
    if kwargs:
        rec["kv"] = kwargs
    try:
        print(json.dumps(rec))
    except Exception:
        print("[%s] %s: %s" % (level, source, message))


def info(source, message, **kw):    _emit("info", source, message, **kw)
def warn(source, message, **kw):    _emit("warning", source, message, **kw)
def error(source, message, **kw):   _emit("error", source, message, **kw)
def debug(source, message, **kw):   _emit("debug", source, message, **kw)
def critical(source, message, **kw): _emit("critical", source, message, **kw)
