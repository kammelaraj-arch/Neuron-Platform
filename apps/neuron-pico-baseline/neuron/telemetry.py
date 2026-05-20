"""Telemetry batching with ring buffer.

A naive app would HTTP-POST every sensor reading — destroys the radio,
floods the master. This batches up to N readings or every T seconds
(whichever first) into one HTTP request. Drops oldest on overflow
rather than blocking the producer.

App usage:

    tb = TelemetryBatcher(channels)
    tb.push({"temp_c": 23.4})        # cheap, just enqueues
    ...
    tb.maybe_flush()                  # called from main loop

The flush is non-blocking — if HTTP is slow, push() keeps working;
old samples drop off the ring tail.
"""
import time


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else 0


class TelemetryBatcher:
    def __init__(self, channels, max_buffer=64, flush_interval_ms=5000,
                 flush_threshold=16):
        self.channels = channels
        self.max_buffer = max_buffer
        self.flush_interval_ms = flush_interval_ms
        self.flush_threshold = flush_threshold
        self._buf = []
        self._last_flush = _ticks_ms()
        self._dropped = 0

    def push(self, fields: dict) -> None:
        rec = {"ts": _ticks_ms(), "fields": fields}
        self._buf.append(rec)
        if len(self._buf) > self.max_buffer:
            # Drop oldest — prefer recent data over old data.
            self._buf.pop(0)
            self._dropped += 1

    def maybe_flush(self) -> bool:
        if not self._buf:
            return False
        now = _ticks_ms()
        if (len(self._buf) < self.flush_threshold and
                now - self._last_flush < self.flush_interval_ms):
            return False
        # Snapshot + clear so push() during flush doesn't lose data.
        snapshot = list(self._buf)
        self._buf.clear()
        payload = {
            "dna":     self.channels.identity.dna,
            "batch":   snapshot,
            "dropped": self._dropped,
        }
        ok = self.channels.api.telemetry(payload)
        if ok:
            self._dropped = 0
            self._last_flush = now
        else:
            # Network failed — re-enqueue the snapshot at the head,
            # but cap to max_buffer to avoid unbounded memory growth.
            self._buf = (snapshot + self._buf)[-self.max_buffer:]
        return ok

    @property
    def queued(self) -> int:
        return len(self._buf)
