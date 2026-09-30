"""Per-endpoint concurrency, spacing and rate-limit cooldown (process local)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

MAX_COOLDOWN_SECONDS = 120.0


class ProviderThrottle:
    def __init__(
        self,
        *,
        max_concurrency: int = 4,
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_concurrency < 1 or min_interval_seconds < 0:
            raise ValueError("throttle bounds must be positive")
        self._max = max_concurrency
        self._interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._semaphore: asyncio.Semaphore | None = None
        self._blocked_until = 0.0
        self._next_start = 0.0
        self.in_flight = 0
        self.peak_in_flight = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max)
        async with self._semaphore:
            while True:
                now = self._clock()
                ready_at = max(self._blocked_until, self._next_start)
                if ready_at <= now:
                    break
                await self._sleep(ready_at - now)
            self._next_start = self._clock() + self._interval
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            try:
                yield
            finally:
                self.in_flight -= 1

    def note_rate_limited(self, retry_after_seconds: float | None) -> float:
        """Apply a shared cooldown; a missing Retry-After still backs off."""
        delay = min(max(retry_after_seconds or 1.0, 0.0), MAX_COOLDOWN_SECONDS)
        self._blocked_until = max(self._blocked_until, self._clock() + delay)
        return delay


class ThrottleRegistry:
    def __init__(self, **defaults: object) -> None:
        self._defaults = defaults
        self._by_endpoint: dict[UUID, ProviderThrottle] = {}

    def for_endpoint(self, endpoint_id: UUID) -> ProviderThrottle:
        throttle = self._by_endpoint.get(endpoint_id)
        if throttle is None:
            throttle = ProviderThrottle(**self._defaults)  # type: ignore[arg-type]
            self._by_endpoint[endpoint_id] = throttle
        return throttle
