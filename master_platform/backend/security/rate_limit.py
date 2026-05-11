from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last: float


class TokenBucket:
    """Per-key token-bucket rate limiter held in-process.

    Suitable for a single Master Platform instance; replace with Redis if
    horizontally scaled.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, rate_per_minute: int, burst: int) -> bool:
        if rate_per_minute <= 0:
            return True
        rate_per_sec = rate_per_minute / 60.0
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(burst), last=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.last)
            bucket.tokens = min(float(burst), bucket.tokens + elapsed * rate_per_sec)
            bucket.last = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False


limiter = TokenBucket()
