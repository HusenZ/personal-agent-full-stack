from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Profile, RefreshToken, RevocationReason, User

pytestmark = pytest.mark.integration

EMAIL = "ada@example.com"
PASSWORD = "a-sufficiently-long-password"


async def register(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post("/api/v1/auth/register", json={"email": email, "password": password})


async def login(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def register_and_login(client: AsyncClient, email: str = EMAIL) -> dict[str, str]:
    await register(client, email=email)
    response = await login(client, email=email)
    tokens: dict[str, str] = response.json()["data"]
    return tokens


class TestRegistration:
    async def test_creates_an_account(self, api_client: AsyncClient):
        response = await register(api_client)

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["email"] == EMAIL
        assert "id" in data and "created_at" in data

    async def test_never_returns_the_password_or_its_hash(self, api_client: AsyncClient):
        response = await register(api_client)

        assert PASSWORD not in response.text
        assert "password" not in response.json()["data"]

    async def test_stores_the_password_only_as_a_hash(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await register(api_client)

        user = (await db_session.execute(select(User))).scalar_one()

        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2")

    async def test_creates_exactly_one_profile_for_the_new_user(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        response = await register(api_client)
        user_id = response.json()["data"]["id"]

        profiles = (await db_session.execute(select(Profile))).scalars().all()

        assert len(profiles) == 1
        assert str(profiles[0].user_id) == user_id

    async def test_rejects_a_duplicate_email(self, api_client: AsyncClient):
        await register(api_client)

        response = await register(api_client)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    async def test_treats_email_case_as_insignificant(self, api_client: AsyncClient):
        await register(api_client, email="Ada@Example.COM")

        response = await register(api_client, email="ada@example.com")

        assert response.status_code == 409

    async def test_normalises_the_stored_email(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await register(api_client, email="  Ada@Example.COM  ")

        user = (await db_session.execute(select(User))).scalar_one()

        assert user.email == "ada@example.com"

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("not-an-email", PASSWORD),
            (EMAIL, "short"),
            (EMAIL, ""),
        ],
    )
    async def test_rejects_invalid_input(self, api_client: AsyncClient, email: str, password: str):
        response = await register(api_client, email=email, password=password)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_no_account_is_created_when_registration_fails(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await register(api_client, password="short")

        assert (await db_session.execute(select(User))).scalars().all() == []


class TestLogin:
    async def test_returns_a_token_pair(self, api_client: AsyncClient):
        await register(api_client)

        response = await login(api_client)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["token_type"] == "bearer"
        assert data["access_token"] and data["refresh_token"]
        assert data["access_token"] != data["refresh_token"]

    async def test_rejects_a_wrong_password(self, api_client: AsyncClient):
        await register(api_client)

        response = await login(api_client, password="not-the-right-password")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

    async def test_unknown_email_is_indistinguishable_from_a_wrong_password(
        self, api_client: AsyncClient
    ):
        """A different code or message here would let anyone enumerate accounts."""
        await register(api_client)

        wrong_password = await login(api_client, password="not-the-right-password")
        unknown_email = await login(api_client, email="nobody@example.com")

        assert unknown_email.status_code == wrong_password.status_code
        assert unknown_email.json()["error"] == wrong_password.json()["error"]

    async def test_rejects_a_deactivated_account(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await register(api_client)
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()

        response = await login(api_client)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INACTIVE_USER"

    async def test_stores_only_the_hash_of_the_refresh_token(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await register(api_client)

        issued = (await login(api_client)).json()["data"]["refresh_token"]
        stored = (await db_session.execute(select(RefreshToken))).scalar_one()

        assert stored.token_hash != issued
        assert issued not in stored.token_hash


class TestRefresh:
    async def test_rotates_the_token_pair(self, api_client: AsyncClient):
        tokens = await register_and_login(api_client)

        response = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert response.status_code == 200
        rotated = response.json()["data"]
        assert rotated["refresh_token"] != tokens["refresh_token"]

    async def test_the_rotated_token_cannot_be_used_again(self, api_client: AsyncClient):
        tokens = await register_and_login(api_client)
        await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        replay = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"

    async def test_replaying_a_rotated_token_revokes_the_whole_family(
        self, api_client: AsyncClient
    ):
        """A leaked token must not leave the attacker's successor usable."""
        original = await register_and_login(api_client)
        current = (
            await api_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
            )
        ).json()["data"]

        await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
        )

        after_breach = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": current["refresh_token"]}
        )

        assert after_breach.status_code == 401

    async def test_reuse_detection_is_recorded(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        original = await register_and_login(api_client)
        await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
        )
        await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
        )

        reasons = (await db_session.execute(select(RefreshToken.revocation_reason))).scalars().all()

        assert RevocationReason.REUSE_DETECTED in reasons

    async def test_rejects_an_unknown_token(self, api_client: AsyncClient):
        response = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "never-issued-by-us"}
        )

        assert response.status_code == 401

    async def test_rejects_an_expired_token(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        tokens = await register_and_login(api_client)
        await db_session.execute(
            update(RefreshToken).values(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
        )
        await db_session.commit()

        response = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert response.status_code == 401

    async def test_rejects_a_token_belonging_to_a_deactivated_account(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        tokens = await register_and_login(api_client)
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()

        response = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert response.status_code == 401


class TestLogout:
    async def test_revokes_the_refresh_token(self, api_client: AsyncClient):
        tokens = await register_and_login(api_client)

        logout = await api_client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        refresh = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert logout.status_code == 204
        assert refresh.status_code == 401

    async def test_revokes_the_rest_of_the_family(self, api_client: AsyncClient):
        original = await register_and_login(api_client)
        current = (
            await api_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
            )
        ).json()["data"]

        await api_client.post(
            "/api/v1/auth/logout", json={"refresh_token": current["refresh_token"]}
        )

        replay = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": current["refresh_token"]}
        )
        assert replay.status_code == 401

    async def test_unknown_token_is_not_an_oracle(self, api_client: AsyncClient):
        """Logout reports the same result whether or not the token ever existed."""
        response = await api_client.post(
            "/api/v1/auth/logout", json={"refresh_token": "never-issued-by-us"}
        )

        assert response.status_code == 204


class TestAccountDeletion:
    async def _sign_in(self, client: AsyncClient, email: str) -> tuple[dict[str, str], str]:
        tokens = await register_and_login(client, email=email)
        return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens["refresh_token"]

    async def test_the_account_and_its_data_are_removed(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers, refresh_token = await self._sign_in(api_client, "ada@example.com")

        response = await api_client.request(
            "DELETE", "/api/v1/auth/account", headers=headers, json={"password": PASSWORD}
        )

        assert response.status_code == 204
        assert await db_session.scalar(select(func.count()).select_from(User)) == 0
        assert await db_session.scalar(select(func.count()).select_from(Profile)) == 0
        assert (await api_client.get("/api/v1/profile", headers=headers)).status_code == 401
        refreshed = await api_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refreshed.status_code == 401
        login = await api_client.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
        )
        assert login.json()["error"]["code"] == "INVALID_CREDENTIALS"

    async def test_a_wrong_password_deletes_nothing_and_keeps_the_session(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers, _ = await self._sign_in(api_client, "ada@example.com")

        response = await api_client.request(
            "DELETE", "/api/v1/auth/account", headers=headers, json={"password": "not-the-password"}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "INCORRECT_PASSWORD"
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1
        assert (await api_client.get("/api/v1/profile", headers=headers)).status_code == 200

    async def test_only_the_signed_in_account_is_deleted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        await self._sign_in(api_client, "grace@example.com")
        headers, _ = await self._sign_in(api_client, "ada@example.com")

        await api_client.request(
            "DELETE", "/api/v1/auth/account", headers=headers, json={"password": PASSWORD}
        )

        emails = (await db_session.scalars(select(User.email))).all()
        assert emails == ["grace@example.com"]

    async def test_deleting_requires_a_token(self, api_client: AsyncClient):
        response = await api_client.request(
            "DELETE", "/api/v1/auth/account", json={"password": PASSWORD}
        )

        assert response.status_code == 401
