"""A sliding-window limiter for the login of the monitoring web.

The CRM is used by a handful of people on a known network; the monitoring web
is reachable by anyone with the link. The generated password is far out of
reach of guessing, but the one the client picks afterwards may not be.

State lives in this process. With a single replica that is the whole story;
behind more than one, each replica enforces its own share of the budget, so a
shared store is needed before scaling out. That is a deliberate trade: an
in-process limiter that works today beats no limiter at all.
"""

import time
from collections import defaultdict, deque

# Attempts allowed per key inside the window, and how long the window lasts.
MAX_ATTEMPTS = 10
WINDOW_SECONDS = 300


class SlidingWindowLimiter:
    """Counts recent attempts per key and refuses once the budget is spent."""

    def __init__(
        self, *, max_attempts: int = MAX_ATTEMPTS, window_seconds: int = WINDOW_SECONDS
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def hit(self, key: str) -> bool:
        """Record an attempt for ``key``; return whether it is allowed."""
        now = time.monotonic()
        hits = self._prune(key, now)
        if len(hits) >= self._max_attempts:
            return False
        hits.append(now)
        return True

    def reset(self, key: str) -> None:
        """Forget a key's attempts, called after a successful login."""
        self._hits.pop(key, None)

    def clear(self) -> None:
        """Drop every key. For tests."""
        self._hits.clear()


# One limiter per process, shared by the login route.
monitor_login_limiter = SlidingWindowLimiter()
