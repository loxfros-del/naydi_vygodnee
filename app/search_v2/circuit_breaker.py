"""Small process-local circuit breaker for optional external providers."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time
from typing import Callable


@dataclass(frozen=True)
class CircuitSnapshot:
    name: str
    open: bool
    blocked_until: float
    reason: str = ""


class ProviderCircuitBreaker:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._states: dict[str, tuple[float, str]] = {}

    def snapshot(self, name: str) -> CircuitSnapshot:
        key = str(name or "").strip().casefold()
        now = self._clock()
        with self._lock:
            blocked_until, reason = self._states.get(key, (0.0, ""))
            if blocked_until <= now:
                self._states.pop(key, None)
                return CircuitSnapshot(key, False, 0.0, "")
            return CircuitSnapshot(key, True, blocked_until, reason)

    def open(self, name: str, *, seconds: float, reason: str = "") -> CircuitSnapshot:
        key = str(name or "").strip().casefold()
        blocked_until = self._clock() + max(1.0, float(seconds))
        with self._lock:
            current_until, _ = self._states.get(key, (0.0, ""))
            if current_until > blocked_until:
                blocked_until = current_until
            self._states[key] = (blocked_until, str(reason or "")[:300])
        return self.snapshot(key)

    def reset(self, name: str) -> None:
        key = str(name or "").strip().casefold()
        with self._lock:
            self._states.pop(key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._states.clear()


shopping_provider_circuit = ProviderCircuitBreaker()


__all__ = ["CircuitSnapshot", "ProviderCircuitBreaker", "shopping_provider_circuit"]
