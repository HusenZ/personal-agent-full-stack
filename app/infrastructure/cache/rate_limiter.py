import logging
import math
import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Sliding window log run atomically in Redis; no boundary bursts or races.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local used = redis.call('ZCARD', key)

if used < limit then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
    return {1, limit - used - 1, 0}
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
redis.call('PEXPIRE', key, window_ms)
return {0, 0, window_ms - (now_ms - tonumber(oldest[2]))}
"""


@dataclass(frozen=True)
class RateLimitRule:
    """How often one class of request may be made."""

    name: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, client: Redis | None, *, fail_open: bool) -> None:
        self._client = client
        self._fail_open = fail_open
        self._script = client.register_script(_SLIDING_WINDOW_SCRIPT) if client else None

    async def check(self, rule: RateLimitRule, identity: str) -> RateLimitDecision:
        """Record one request against a rule and say whether it may proceed."""
        if self._script is None:
            return self._unavailable(rule, reason="redis_not_configured")

        key = f"ratelimit:{rule.name}:{identity}"
        window_ms = rule.window_seconds * 1000

        try:
            raw = await self._script(
                keys=[key],
                args=[int(time.time() * 1000), window_ms, rule.limit, uuid.uuid4().hex],
            )
        except (RedisError, OSError, TimeoutError):
            logger.error("rate_limit_backend_unavailable", extra={"rule": rule.name})
            return self._unavailable(rule, reason="redis_unavailable")

        allowed, remaining, retry_after_ms = (int(value) for value in raw)

        return RateLimitDecision(
            allowed=bool(allowed),
            limit=rule.limit,
            remaining=remaining,
            # Round up so waiting exactly this long is enough.
            retry_after_seconds=max(1, math.ceil(retry_after_ms / 1000)) if not allowed else 0,
        )

    def _unavailable(self, rule: RateLimitRule, reason: str) -> RateLimitDecision:
        """Decide what to do when the limiter cannot reach its backend.

        Failing open trades a window of unenforced limits for keeping login and
        every other protected endpoint working during a cache outage. Failing
        closed is available for deployments that would rather be unavailable
        than unprotected.
        """
        if not self._fail_open:
            logger.error("rate_limit_failing_closed", extra={"rule": rule.name, "reason": reason})
            return RateLimitDecision(
                allowed=False,
                limit=rule.limit,
                remaining=0,
                retry_after_seconds=rule.window_seconds,
            )

        return RateLimitDecision(
            allowed=True, limit=rule.limit, remaining=rule.limit, retry_after_seconds=0
        )
