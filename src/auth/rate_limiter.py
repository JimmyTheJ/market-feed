"""Rate limiting for brute force protection.

Implements dual-layer limiting:
- Per-IP: catches distributed attacks on multiple accounts
- Per-username: catches targeted attacks on a single account

All auth events are logged in a fail2ban-compatible format.
"""

import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)


class RateLimiter:
    """In-memory rate limiter that tracks attempts per key."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lockouts: dict[str, float] = {}
        self._lock = Lock()

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if key is allowed to attempt.

        Returns (allowed, seconds_until_unlock).
        """
        with self._lock:
            now = time.time()

            if key in self._lockouts:
                unlock_at = self._lockouts[key]
                if now < unlock_at:
                    return False, int(unlock_at - now)
                else:
                    # Lockout expired
                    del self._lockouts[key]
                    self._attempts.pop(key, None)

            return True, 0

    def record_failure(self, key: str) -> tuple[bool, int]:
        """Record a failed attempt.

        Returns (now_locked_out, attempts_remaining_or_lockout_seconds).
        """
        with self._lock:
            now = time.time()

            if key not in self._attempts:
                self._attempts[key] = []

            # Clean old attempts outside window
            cutoff = now - self.lockout_seconds
            self._attempts[key] = [t for t in self._attempts[key] if t > cutoff]
            self._attempts[key].append(now)

            if len(self._attempts[key]) >= self.max_attempts:
                self._lockouts[key] = now + self.lockout_seconds
                logger.warning(
                    f"Rate limit lockout: key={key} duration={self.lockout_seconds}s"
                )
                return True, self.lockout_seconds

            remaining = self.max_attempts - len(self._attempts[key])
            return False, remaining

    def record_success(self, key: str):
        """Clear tracking on successful authentication."""
        with self._lock:
            self._attempts.pop(key, None)
            self._lockouts.pop(key, None)

    def cleanup(self, max_age: float = 3600):
        """Remove stale entries older than max_age seconds."""
        with self._lock:
            now = time.time()
            stale_keys = [
                k
                for k, v in self._attempts.items()
                if not v or (now - max(v)) > max_age
            ]
            for k in stale_keys:
                del self._attempts[k]

            expired_lockouts = [
                k for k, v in self._lockouts.items() if now > v
            ]
            for k in expired_lockouts:
                del self._lockouts[k]

    @property
    def active_lockouts(self) -> int:
        """Number of currently active lockouts."""
        with self._lock:
            now = time.time()
            return sum(1 for v in self._lockouts.values() if now < v)
