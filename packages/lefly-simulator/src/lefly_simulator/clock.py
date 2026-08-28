"""Clock abstractions for deterministic simulator behavior."""

import asyncio
import math
import time
from typing import List, Protocol, Tuple


def _validate_sleep_duration(seconds: object) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TypeError("sleep duration must be numeric")
    if not math.isfinite(seconds):
        raise ValueError("sleep duration must be finite")
    if seconds < 0:
        raise ValueError("sleep duration cannot be negative")
    return float(seconds)


class Clock(Protocol):
    """Time source used by asynchronous simulator components."""

    def now(self) -> float:
        ...

    async def sleep(self, seconds: float) -> None:
        ...


class RealClock:
    """Clock backed by the process monotonic clock."""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        duration = _validate_sleep_duration(seconds)
        if duration == 0:
            return
        await asyncio.sleep(duration)


class ManualClock:
    """Monotonic clock advanced explicitly by tests or a simulator driver."""

    def __init__(self, initial: float = 0.0) -> None:
        if isinstance(initial, bool) or not isinstance(initial, (int, float)):
            raise TypeError("initial time must be numeric")
        if not math.isfinite(initial):
            raise ValueError("initial time must be finite")
        self._now = float(initial)
        self._waiters: List[Tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self._now

    @property
    def pending_sleep_count(self) -> int:
        return len(self._waiters)

    async def sleep(self, seconds: float) -> None:
        duration = _validate_sleep_duration(seconds)
        if duration == 0:
            return

        future = asyncio.get_running_loop().create_future()
        waiter = (self._now + duration, future)
        self._waiters.append(waiter)
        try:
            await future
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def advance(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError("advance duration must be numeric")
        if not math.isfinite(seconds):
            raise ValueError("advance duration must be finite")
        if seconds < 0:
            raise ValueError("cannot move a monotonic clock backwards")

        self._now += float(seconds)
        ready = [waiter for waiter in self._waiters if waiter[0] <= self._now]
        self._waiters = [waiter for waiter in self._waiters if waiter[0] > self._now]
        for _, future in ready:
            if not future.done():
                future.set_result(None)
