"""Offline buffer — queues telemetry while the Edge is unreachable, flushes
on reconnect. Default policy from the Brain config is ``continue_safe``: keep
running local control loops, queue telemetry up to ``buffer_size`` records.
"""

class OfflineQueue:
    def __init__(self, buffer_size=128):
        self._buf = []
        self._max = buffer_size

    def enqueue(self, payload):
        if len(self._buf) >= self._max:
            self._buf.pop(0)  # drop oldest
        self._buf.append(payload)

    def flush(self, sender):
        if not self._buf:
            return 0
        sent = 0
        # Walk a copy so concurrent enqueue (next loop tick) doesn't race.
        for payload in list(self._buf):
            ok = sender(payload)
            if not ok:
                break
            sent += 1
        del self._buf[:sent]
        return sent

    def __len__(self):
        return len(self._buf)
