"""Rate limits, and the dependencies that apply them.

The limits live next to the routes they protect rather than in configuration,
because which endpoint deserves which ceiling is a design decision about this
API, not something an operator should be able to quietly relax.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Response

from app.api.dependencies import CurrentUserDep, RateLimiterDep, SettingsDep
from app.core.exceptions import RateLimitedError
from app.infrastructure.cache.rate_limiter import RateLimitRule
from app.infrastructure.database.repositories.user import normalise_email
from app.schemas.auth import LoginRequest

logger = logging.getLogger(__name__)

# A burst of registrations from one address is abuse or a mistake.
REGISTER_PER_IP = RateLimitRule(name="register_ip", limit=5, window_seconds=3600)

# Covers one address trying many accounts and many addresses trying one.
LOGIN_PER_IP = RateLimitRule(name="login_ip", limit=10, window_seconds=900)
LOGIN_PER_ACCOUNT = RateLimitRule(name="login_account", limit=5, window_seconds=900)

# Only there to stop a client stuck in a refresh loop.
REFRESH_PER_IP = RateLimitRule(name="refresh_ip", limit=60, window_seconds=3600)

# These call a paid model, so the limits are about cost as well as abuse.
ONBOARDING_MESSAGE_PER_USER = RateLimitRule(
    name="onboarding_message_user", limit=60, window_seconds=3600
)
ANALYSIS_PER_USER = RateLimitRule(name="analysis_user", limit=10, window_seconds=3600)
PLAN_PER_USER = RateLimitRule(name="plan_user", limit=10, window_seconds=3600)
CHAT_PER_USER = RateLimitRule(name="chat_user", limit=120, window_seconds=3600)

# Deletion checks a password, so it's a guessing surface for stolen tokens.
DELETE_ACCOUNT_PER_USER = RateLimitRule(name="delete_account_user", limit=5, window_seconds=900)

type RateLimitDependency = Callable[..., Coroutine[Any, Any, None]]


def client_address(request: Request, trust_proxy_headers: bool) -> str:
    """Identify the caller by network address.

    X-Forwarded-For is only consulted when the deployment says a proxy it
    controls sets it. Trusting it otherwise would let any caller bypass a
    per-address limit simply by sending the header.
    """
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


async def _enforce(
    rule: RateLimitRule,
    identity: str,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> None:
    if not settings.rate_limit_enabled:
        return

    decision = await limiter.check(rule, identity)

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)

    if not decision.allowed:
        logger.warning("rate_limit_exceeded", extra={"rule": rule.name})
        raise RateLimitedError(retry_after_seconds=decision.retry_after_seconds)


def limit_by_address(rule: RateLimitRule) -> RateLimitDependency:
    async def dependency(
        request: Request, response: Response, limiter: RateLimiterDep, settings: SettingsDep
    ) -> None:
        identity = client_address(request, settings.trust_proxy_headers)
        await _enforce(rule, f"ip:{identity}", limiter, settings, response)

    return dependency


def limit_by_user(rule: RateLimitRule) -> RateLimitDependency:
    async def dependency(
        response: Response,
        current_user: CurrentUserDep,
        limiter: RateLimiterDep,
        settings: SettingsDep,
    ) -> None:
        await _enforce(rule, f"user:{current_user.id}", limiter, settings, response)

    return dependency


def limit_login_by_account(rule: RateLimitRule) -> RateLimitDependency:
    async def dependency(
        payload: LoginRequest,
        response: Response,
        limiter: RateLimiterDep,
        settings: SettingsDep,
    ) -> None:
        # Keyed on the target account so distributed guessing is still bounded.
        await _enforce(
            rule, f"account:{normalise_email(payload.email)}", limiter, settings, response
        )

    return dependency


RegisterRateLimit = Annotated[None, Depends(limit_by_address(REGISTER_PER_IP))]
LoginAddressRateLimit = Annotated[None, Depends(limit_by_address(LOGIN_PER_IP))]
LoginAccountRateLimit = Annotated[None, Depends(limit_login_by_account(LOGIN_PER_ACCOUNT))]
RefreshRateLimit = Annotated[None, Depends(limit_by_address(REFRESH_PER_IP))]
OnboardingMessageRateLimit = Annotated[None, Depends(limit_by_user(ONBOARDING_MESSAGE_PER_USER))]
AnalysisRateLimit = Annotated[None, Depends(limit_by_user(ANALYSIS_PER_USER))]
PlanRateLimit = Annotated[None, Depends(limit_by_user(PLAN_PER_USER))]
ChatRateLimit = Annotated[None, Depends(limit_by_user(CHAT_PER_USER))]
DeleteAccountRateLimit = Annotated[None, Depends(limit_by_user(DELETE_ACCOUNT_PER_USER))]
