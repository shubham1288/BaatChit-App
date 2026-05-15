import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status

from app.core.config import settings


class InMemoryRateLimiter:
    """
    Sliding-window rate limiter backed by in-memory storage.
    Thread-safe. Not suitable for multi-process deployments
    (use Redis-backed limiter for horizontal scaling).
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        # username → deque of timestamps
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window

        with self._lock:
            bucket = self._buckets[key]
            # Remove timestamps outside the window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self._max_calls:
                return False

            bucket.append(now)
            return True

    def check(self, key: str) -> None:
        """Raise 429 if the key has exceeded the rate limit."""
        if not self.is_allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: max {self._max_calls} requests "
                    f"per {self._window}s"
                ),
            )


# Singleton rate limiter for message sending
message_rate_limiter = InMemoryRateLimiter(
    max_calls=settings.rate_limit_max_messages,
    window_seconds=settings.rate_limit_window_seconds,
)
