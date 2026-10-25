"""Per-caller question rate limit (docs/architecture.md §3: 10/min anonymous, 30/min signed in).

In-process sliding window: correct for the single API process of the MVP. With several API replicas it becomes
a per-replica limit; a shared store (Postgres or Redis) is the P1 upgrade when the API scales out.
"""

import math
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    WINDOW_SECONDS = 60.0

    def __init__(self) -> None:
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> int | None:
        """Record a hit. Returns None if allowed, otherwise the seconds until the next request is allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(self.WINDOW_SECONDS - (now - hits[0])))
            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class FailureLock:
    """Locks an account after `limit` failed sign-ins within `window` seconds, for the rest of that window
    (§11.2 "limit … by user"). Success clears the count. Same per-process caveat as RateLimiter."""

    def __init__(self) -> None:
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, window: float, now: float) -> deque[float]:
        failures = self._failures[key]
        while failures and now - failures[0] >= window:
            failures.popleft()
        return failures

    def locked_for(self, key: str, limit: int, window: float, now: float | None = None) -> int | None:
        """Seconds until the account unlocks, or None when it may try again."""
        now = time.monotonic() if now is None else now
        with self._lock:
            failures = self._prune(key, window, now)
            if len(failures) >= limit:
                return max(1, math.ceil(window - (now - failures[0])))
            return None

    def fail(self, key: str, window: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(key, window, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


limiter = RateLimiter()
login_failures = FailureLock()
order_lookup_failures = FailureLock()  # US-05: failed order verifications, per caller and per order number
