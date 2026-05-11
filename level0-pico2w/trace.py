"""Lightweight trace ring buffer flushed to the edge on heartbeat / OTA."""

_BUF = []
_MAX = 64


def add(event, **kv):
    rec = {"e": event}
    if kv:
        rec.update(kv)
    _BUF.append(rec)
    if len(_BUF) > _MAX:
        _BUF.pop(0)


def drain():
    out = list(_BUF)
    del _BUF[:]
    return out


def peek():
    return list(_BUF)
