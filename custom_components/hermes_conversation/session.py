"""Map each voice satellite onto a Hermes session until it goes idle."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

SESSION_ID_PREFIX = "ha-"


@dataclass
class _Session:
    session_id: str
    last_used: float


class SessionTracker:
    """Hand out one Hermes session per origin, minting a new one after idling.

    Hermes derives its own session id from the system prompt and the first
    user message, so two unrelated "turn off the light" commands would land in
    the same session. Minting ids here keeps that decision in Home Assistant,
    where the idle timeout lives.
    """

    def __init__(
        self,
        idle_timeout: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Track sessions that expire after ``idle_timeout`` seconds unused."""
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._sessions: dict[str, _Session] = {}

    @property
    def enabled(self) -> bool:
        """Return whether turns are ever joined into a session."""
        return self._idle_timeout > 0

    def session_for(self, origin: str) -> str:
        """Return the live session for ``origin``, or start a fresh one."""
        now = self._clock()
        self._prune(now)

        session = self._sessions.get(origin)
        if session is not None:
            session.last_used = now
            return session.session_id

        session = _Session(f"{SESSION_ID_PREFIX}{uuid.uuid4().hex}", now)
        self._sessions[origin] = session
        return session.session_id

    def _prune(self, now: float) -> None:
        expired = [
            origin
            for origin, session in self._sessions.items()
            if now - session.last_used >= self._idle_timeout
        ]
        for origin in expired:
            del self._sessions[origin]
