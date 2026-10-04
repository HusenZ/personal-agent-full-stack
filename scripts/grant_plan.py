"""Put an account on a plan by hand, until a billing provider does it.

    uv run python -m scripts.grant_plan ada@example.com pro --until 2026-12-31
    uv run python -m scripts.grant_plan ada@example.com pro          # no end date
    uv run python -m scripts.grant_plan ada@example.com free         # back to free

Writes to the database in DATABASE_URL. `--until` is a UTC date: the plan ends
at the start of that day.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from app.application.subscription.service import SubscriptionService
from app.core.config import get_settings
from app.domain.subscription.plans import Plan
from app.infrastructure.database.repositories.subscription import SubscriptionRepository
from app.infrastructure.database.repositories.user import UserRepository
from app.infrastructure.database.session import create_database_engine, create_session_factory


async def grant(email: str, plan: Plan, expires_at: datetime | None) -> None:
    engine = create_database_engine(get_settings())
    try:
        async with create_session_factory(engine)() as session:
            user = await UserRepository(session).get_by_email(email)
            if user is None:
                raise SystemExit(f"no account with the email {email}")
            service = SubscriptionService(session, SubscriptionRepository(session))
            await service.grant(user.id, plan, expires_at=expires_at)
            view = await service.current(user.id)
    finally:
        await engine.dispose()

    ends = view.expires_at.isoformat() if view.expires_at else "no end date"
    print(f"{email}: {view.plan.value} ({ends})")


def _utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("plan", type=Plan, choices=list(Plan))
    parser.add_argument("--until", type=_utc_date, default=None, help="UTC date, YYYY-MM-DD")
    arguments = parser.parse_args()

    if arguments.until is not None and arguments.until <= datetime.now(tz=UTC):
        parser.error("--until must be in the future")
    asyncio.run(grant(arguments.email, arguments.plan, arguments.until))


if __name__ == "__main__":
    main()
