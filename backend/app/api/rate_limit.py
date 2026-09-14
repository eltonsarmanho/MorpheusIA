from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable


class RateLimiter:
    """In-memory, single-process sliding-window limiter.

    Enforces both a per-minute rate and a per-session total cap (spec AC
    CHAT-10), tracked independently per `session_id`. Single-process scope
    only - see design.md Risks & Concerns for the multi-replica caveat.
    """

    def __init__(
        self,
        per_minute: int,
        per_session: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._per_minute = per_minute
        self._per_session = per_session
        self._clock = clock
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def check(self, session_id: str) -> bool:
        """Return True and record the call if `session_id` is under both
        limits; return False (without recording) if either limit is already
        exceeded.
        """
        now = self._clock() if self._clock is not None else time.time()
        history = self._timestamps[session_id]

        if len(history) >= self._per_session:
            return False

        window_start = now - 60
        recent_count = sum(1 for t in history if t > window_start)
        if recent_count >= self._per_minute:
            return False

        history.append(now)
        return True
