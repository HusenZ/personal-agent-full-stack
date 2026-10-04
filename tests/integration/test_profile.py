import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import User

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    """Register a user and return the Authorization header for them."""
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


class TestProfileAuthorization:
    async def test_requires_a_token(self, api_client: AsyncClient):
        response = await api_client.get("/api/v1/profile")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_rejects_a_malformed_token(self, api_client: AsyncClient):
        response = await api_client.get(
            "/api/v1/profile", headers={"Authorization": "Bearer not-a-real-token"}
        )

        assert response.status_code == 401

    async def test_rejects_a_token_from_another_signing_key(self, api_client: AsyncClient):
        from pydantic import SecretStr

        from app.core.security import create_access_token

        forged, _ = create_access_token(uuid.uuid4(), SecretStr("f" * 48), "HS256", ttl_seconds=900)

        response = await api_client.get(
            "/api/v1/profile", headers={"Authorization": f"Bearer {forged}"}
        )

        assert response.status_code == 401

    async def test_rejects_a_token_whose_user_no_longer_exists(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """A valid signature is not enough; the account is re-checked per request."""
        headers = await authenticate(api_client, "ada@example.com")
        await db_session.execute(delete(User))
        await db_session.commit()

        response = await api_client.get("/api/v1/profile", headers=headers)

        assert response.status_code == 401

    async def test_rejects_a_token_for_a_deactivated_account(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        from sqlalchemy import update

        headers = await authenticate(api_client, "ada@example.com")
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()

        response = await api_client.get("/api/v1/profile", headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INACTIVE_USER"


class TestProfileReadAndWrite:
    async def test_a_new_profile_is_empty(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get("/api/v1/profile", headers=headers)

        assert response.status_code == 200
        assert response.json()["data"] == {
            "timezone": "UTC",
            "goals": [],
            "constraints": [],
            "preferences": {},
        }

    async def test_updates_every_collection(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={
                "goals": [{"name": "Transition into AI engineering", "priority": "high"}],
                "constraints": ["Limited weekday availability"],
                "preferences": {"communication_style": "direct"},
            },
        )

        assert response.status_code == 200
        assert response.json()["data"] == {
            "timezone": "UTC",
            "goals": [{"name": "Transition into AI engineering", "priority": "high"}],
            "constraints": ["Limited weekday availability"],
            "preferences": {"communication_style": "direct"},
        }

    async def test_the_update_is_persisted(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.patch(
            "/api/v1/profile", headers=headers, json={"constraints": ["No travel"]}
        )

        response = await api_client.get("/api/v1/profile", headers=headers)

        assert response.json()["data"]["constraints"] == ["No travel"]

    async def test_goal_order_is_preserved(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        names = [f"Goal {index}" for index in range(10)]

        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"goals": [{"name": name, "priority": "low"} for name in names]},
        )
        response = await api_client.get("/api/v1/profile", headers=headers)

        assert [goal["name"] for goal in response.json()["data"]["goals"]] == names

    async def test_an_omitted_collection_is_left_untouched(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"constraints": ["No travel"], "preferences": {"style": "direct"}},
        )

        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"goals": [{"name": "Ship the backend", "priority": "high"}]},
        )
        response = await api_client.get("/api/v1/profile", headers=headers)

        data = response.json()["data"]
        assert data["constraints"] == ["No travel"]
        assert data["preferences"] == {"style": "direct"}
        assert len(data["goals"]) == 1

    async def test_an_empty_list_clears_a_collection(self, api_client: AsyncClient):
        """Absent and empty must mean different things, or clearing is impossible."""
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.patch(
            "/api/v1/profile", headers=headers, json={"constraints": ["No travel"]}
        )

        await api_client.patch("/api/v1/profile", headers=headers, json={"constraints": []})
        response = await api_client.get("/api/v1/profile", headers=headers)

        assert response.json()["data"]["constraints"] == []

    async def test_replacing_goals_does_not_accumulate_rows(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        for _ in range(3):
            await api_client.patch(
                "/api/v1/profile",
                headers=headers,
                json={"goals": [{"name": "Only goal", "priority": "medium"}]},
            )
        response = await api_client.get("/api/v1/profile", headers=headers)

        assert len(response.json()["data"]["goals"]) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"goals": [{"name": "", "priority": "high"}]},
            {"goals": [{"name": "Valid", "priority": "urgent"}]},
            {"goals": [{"name": f"Goal {i}", "priority": "low"} for i in range(51)]},
            {"constraints": [""]},
            {"preferences": {"": "value"}},
        ],
    )
    async def test_rejects_invalid_payloads(
        self, api_client: AsyncClient, payload: dict[str, object]
    ):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.patch("/api/v1/profile", headers=headers, json=payload)

        assert response.status_code == 422


class TestUserIsolation:
    async def test_one_user_cannot_see_another_users_profile(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await api_client.patch(
            "/api/v1/profile", headers=ada, json={"constraints": ["Ada's constraint"]}
        )

        response = await api_client.get("/api/v1/profile", headers=grace)

        assert response.json()["data"]["constraints"] == []

    async def test_one_user_cannot_overwrite_another_users_profile(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await api_client.patch(
            "/api/v1/profile",
            headers=ada,
            json={"goals": [{"name": "Ada's goal", "priority": "high"}]},
        )

        await api_client.patch(
            "/api/v1/profile",
            headers=grace,
            json={"goals": [{"name": "Grace's goal", "priority": "low"}]},
        )

        ada_profile = (await api_client.get("/api/v1/profile", headers=ada)).json()["data"]
        grace_profile = (await api_client.get("/api/v1/profile", headers=grace)).json()["data"]

        assert [goal["name"] for goal in ada_profile["goals"]] == ["Ada's goal"]
        assert [goal["name"] for goal in grace_profile["goals"]] == ["Grace's goal"]

    async def test_clearing_one_profile_leaves_the_other_intact(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        for headers in (ada, grace):
            await api_client.patch(
                "/api/v1/profile", headers=headers, json={"constraints": ["Shared text"]}
            )

        await api_client.patch("/api/v1/profile", headers=grace, json={"constraints": []})

        ada_profile = (await api_client.get("/api/v1/profile", headers=ada)).json()["data"]
        assert ada_profile["constraints"] == ["Shared text"]


class TestAuthenticationChallenge:
    async def test_unauthenticated_responses_advertise_the_scheme(self, api_client: AsyncClient):
        """RFC 6750: a 401 must tell the client which scheme to authenticate with."""
        response = await api_client.get("/api/v1/profile")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_non_auth_errors_do_not_advertise_a_scheme(self, api_client: AsyncClient):
        response = await api_client.get("/api/v1/does-not-exist")

        assert response.status_code == 404
        assert "www-authenticate" not in response.headers
