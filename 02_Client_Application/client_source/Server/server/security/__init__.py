"""Password hashing, reconnect tokens and rate limiting.

Passwords are never stored or transmitted in plain text.  They are hashed with
PBKDF2-HMAC-SHA256 using a per-session random salt.  Reconnect tokens are
random 256-bit values; only their SHA-256 digest is stored in SQLite.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import deque
from typing import Deque

PBKDF2_ITERATIONS = 210_000
_SALT_BYTES = 16


def make_salt() -> str:
    return secrets.token_hex(_SALT_BYTES)


def hash_password(password: str, salt: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    if password is None:
        password = ""
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    try:
        actual = hash_password(password, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_hash or "")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash or "")


def random_token() -> str:
    return secrets.token_urlsafe(32)


class RateLimiter:
    """Small token bucket suitable for per-connection message limiting."""

    def __init__(self, rate: float = 400.0, capacity: float = 800.0) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def retry_after(self) -> float:
        if self.tokens >= 1:
            return 0.0
        return (1.0 - self.tokens) / self.rate


class SlidingWindowCounter:
    """Tracks recent slow operations for diagnostics."""

    def __init__(self, seconds: int = 60) -> None:
        self.seconds = seconds
        self.samples: Deque[float] = deque()

    def add(self, value: float) -> None:
        now = time.monotonic()
        self.samples.append(value)
        while self.samples and len(self.samples) > 5000:
            self.samples.popleft()
        _ = now

    def average(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else 0.0

    def percentile(self, fraction: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, max(0, int(fraction * len(ordered)) - 1))
        return ordered[index]
