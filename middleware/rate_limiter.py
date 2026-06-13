"""
MediVerse - Rate Limiter (unified, used by all services)
"""

import time
from collections import defaultdict
from config.settings import settings


class RateLimiter:
    """Simple in-memory rate limiter by IP or identifier."""

    def __init__(self, calls: int = None, period: int = None):
        self.calls = calls or settings.rate_limit.CALLS
        self.period = period or settings.rate_limit.PERIOD
        self.requests: dict = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        self.requests[key] = [
            t for t in self.requests[key] if now - t < self.period
        ]
        if len(self.requests[key]) >= self.calls:
            return False
        self.requests[key].append(now)
        return True

    def get_remaining(self, key: str) -> int:
        now = time.time()
        self.requests[key] = [
            t for t in self.requests[key] if now - t < self.period
        ]
        return max(0, self.calls - len(self.requests[key]))


# Global instance
rate_limiter = RateLimiter()
