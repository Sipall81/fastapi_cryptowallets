import json

from httpx import AsyncClient

from tests.helpers import login_as


class TestAdminAccess:

    async def test_regular_user_cannot_block(
        self, client: AsyncClient, regular_user, admin_user
    ):
        await login_as(client, regular_user)
        response = await client.delete(
            f"/admin/users/{admin_user.id}/block",
            params={"reason": "test"},
        )
        assert response.status_code == 403

    async def test_admin_endpoints_require_auth(self, client: AsyncClient, regular_user):
        uid = regular_user.id
        responses = [
            await client.get("/admin/users"),
            await client.get(f"/admin/user/{uid}"),
            await client.post("/admin/give_admin", params={"user_id": uid}),
            await client.post(f"/admin/users/{uid}/unblock"),
            await client.delete(f"/admin/users/{uid}/block", params={"reason": "x"}),
            await client.patch(f"/admin/change_user_data/{uid}", params={"role": "user"}),
        ]
        assert [r.status_code for r in responses] == [401] * 6


class TestBlockUser:

    async def test_admin_can_block(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        response = await admin_client.delete(
            f"/admin/users/{regular_user.id}/block",
            params={"reason": "spam"},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == regular_user.id

        await test_session.refresh(regular_user)
        assert regular_user.is_blocked is True
        assert regular_user.block_reason == "spam"
        assert regular_user.blocked_at is not None

    async def test_block_unknown_user(self, admin_client: AsyncClient):
        response = await admin_client.delete(
            "/admin/users/99999/block", params={"reason": "spam"}
        )
        assert response.status_code == 404

    async def test_block_requires_reason(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.delete(f"/admin/users/{regular_user.id}/block")
        assert response.status_code == 422

    async def test_block_already_blocked(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await admin_client.delete(
            f"/admin/users/{regular_user.id}/block",
            params={"reason": "again"},
        )
        assert response.status_code == 400


class TestUnblockUser:

    async def test_admin_can_unblock(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        regular_user.block_reason = "spam"
        await test_session.commit()

        response = await admin_client.post(f"/admin/users/{regular_user.id}/unblock")
        assert response.status_code == 200
        assert "unblocked" in response.json()["info"].lower()

        await test_session.refresh(regular_user)
        assert regular_user.is_blocked is False
        assert regular_user.blocked_at is None
        assert regular_user.block_reason is None

    async def test_unblock_not_blocked_user(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.post(f"/admin/users/{regular_user.id}/unblock")
        assert response.status_code == 400

    async def test_unblock_unknown_user(self, admin_client: AsyncClient):
        response = await admin_client.post("/admin/users/99999/unblock")
        assert response.status_code == 404

    async def test_regular_user_cannot_unblock(
        self, client: AsyncClient, regular_user, admin_user
    ):
        await login_as(client, regular_user)
        response = await client.post(f"/admin/users/{admin_user.id}/unblock")
        assert response.status_code == 403


class TestListUsers:

    async def test_list_users(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.get("/admin/users")
        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)
        assert len(users) >= 2

    async def test_list_users_filter_blocked(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await admin_client.get("/admin/users", params={"blocked": True})
        assert response.status_code == 200
        users = response.json()
        assert len(users) == 1
        assert all(u["is_blocked"] for u in users)

    async def test_list_users_filter_not_blocked(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await admin_client.get("/admin/users", params={"blocked": False})
        assert response.status_code == 200
        emails = {u["email"] for u in response.json()}
        assert regular_user.email not in emails

    async def test_list_users_filter_by_role(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.get("/admin/users", params={"role": "admin"})
        assert response.status_code == 200
        users = response.json()
        assert len(users) == 1
        assert all(u["role"] == "admin" for u in users)

    async def test_list_users_pagination(self, admin_client: AsyncClient, regular_user):
        first_page = await admin_client.get("/admin/users", params={"limit": 1})
        second_page = await admin_client.get(
            "/admin/users", params={"limit": 1, "offset": 1}
        )
        assert len(first_page.json()) == 1
        assert len(second_page.json()) == 1
        assert first_page.json()[0]["id"] != second_page.json()[0]["id"]

    async def test_list_users_limit_is_capped(self, admin_client: AsyncClient):
        response = await admin_client.get("/admin/users", params={"limit": 201})
        assert response.status_code == 422

    async def test_list_users_negative_offset_rejected(self, admin_client: AsyncClient):
        response = await admin_client.get("/admin/users", params={"offset": -1})
        assert response.status_code == 422

    async def test_regular_user_cannot_list_users(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        response = await client.get("/admin/users")
        assert response.status_code == 403


class TestAdminUserInfo:

    async def test_returns_admin_view_of_user(
        self, admin_client: AsyncClient, regular_user
    ):
        response = await admin_client.get(f"/admin/user/{regular_user.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == regular_user.email
        assert data["role"] == "user"
        assert data["is_blocked"] is False
        assert "hashed_password" not in data

    async def test_unknown_user(self, admin_client: AsyncClient):
        response = await admin_client.get("/admin/user/99999")
        assert response.status_code == 404

    async def test_result_is_cached_for_60_seconds(
        self, admin_client: AsyncClient, regular_user, override_redis
    ):
        await admin_client.get(f"/admin/user/{regular_user.id}")

        override_redis.setex.assert_awaited_once()
        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == f"from_admin:user:{regular_user.id}"
        assert ttl == 60

    async def test_served_from_cache(self, admin_client: AsyncClient, override_redis):
        override_redis.get.return_value = json.dumps(
            {
                "id": 999,
                "name": "Cached",
                "email": "cached@example.com",
                "role": "user",
                "is_blocked": False,
                "blocked_at": None,
                "block_reason": None,
            }
        )
        response = await admin_client.get("/admin/user/999")
        assert response.status_code == 200
        assert response.json()["name"] == "Cached"

    async def test_regular_user_forbidden(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        response = await client.get(f"/admin/user/{regular_user.id}")
        assert response.status_code == 403


class TestGiveAdmin:

    async def test_give_admin(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        response = await admin_client.post(
            "/admin/give_admin", params={"user_id": regular_user.id}
        )
        assert response.status_code == 200
        assert response.json()["user_role"] == "admin"

        await test_session.refresh(regular_user)
        assert regular_user.role == "admin"

    async def test_give_admin_to_admin(self, admin_client: AsyncClient, admin_user):
        response = await admin_client.post(
            "/admin/give_admin", params={"user_id": admin_user.id}
        )
        assert response.status_code == 200
        assert "already admin" in response.json()["info"]

    async def test_give_admin_unknown_user(self, admin_client: AsyncClient):
        response = await admin_client.post("/admin/give_admin", params={"user_id": 99999})
        assert response.status_code == 404

    async def test_regular_user_cannot_give_admin(
        self, client: AsyncClient, regular_user
    ):
        await login_as(client, regular_user)
        response = await client.post(
            "/admin/give_admin", params={"user_id": regular_user.id}
        )
        assert response.status_code == 403


class TestChangeUserData:
    """
    Ручка читает поля через Depends(), поэтому данные передаются query-параметрами.

    Во всех «успешных» тестах передаётся role: ручка сейчас падает с KeyError,
    если role не указана (см. test_known_bugs.py).
    """

    async def test_change_name_and_role(
        self, admin_client: AsyncClient, regular_user, test_session
    ):
        response = await admin_client.patch(
            f"/admin/change_user_data/{regular_user.id}",
            params={"name": "Renamed", "role": "admin"},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == regular_user.id

        await test_session.refresh(regular_user)
        assert regular_user.name == "Renamed"
        assert regular_user.role == "admin"

    async def test_no_changes(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.patch(f"/admin/change_user_data/{regular_user.id}")
        assert response.status_code == 200
        assert response.json()["info"] == "No changes were made"

    async def test_invalid_role(self, admin_client: AsyncClient, regular_user):
        response = await admin_client.patch(
            f"/admin/change_user_data/{regular_user.id}",
            params={"role": "superuser"},
        )
        assert response.status_code == 422
        assert "Invalid role" in response.json()["detail"]

    async def test_email_already_in_use(
        self, admin_client: AsyncClient, regular_user, admin_user
    ):
        response = await admin_client.patch(
            f"/admin/change_user_data/{regular_user.id}",
            params={"email": admin_user.email, "role": "user"},
        )
        assert response.status_code == 400
        assert "already in use" in response.json()["detail"]

    async def test_unknown_user(self, admin_client: AsyncClient):
        response = await admin_client.patch(
            "/admin/change_user_data/99999", params={"role": "user"}
        )
        assert response.status_code == 404

    async def test_invalidates_caches(
        self, admin_client: AsyncClient, regular_user, override_redis
    ):
        await admin_client.patch(
            f"/admin/change_user_data/{regular_user.id}",
            params={"name": "Renamed", "role": "user"},
        )
        override_redis.delete.assert_awaited_once_with(
            f"from_admin:user:{regular_user.id}",
            f"from_user:user:{regular_user.id}",
            "users:all",
        )

    async def test_regular_user_forbidden(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        response = await client.patch(
            f"/admin/change_user_data/{regular_user.id}", params={"role": "admin"}
        )
        assert response.status_code == 403


class TestBlockedUser:

    async def test_blocked_user_can_login_but_is_locked_out(
        self, client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        regular_user.block_reason = "test"
        await test_session.commit()

        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "password123"},
        )
        # Логин проходит (проверка блокировки — в get_current_user)
        assert response.status_code == 200

        # Но защищённые ручки недоступны
        response = await client.get("/users/me")
        assert response.status_code == 403
        assert "blocked" in response.json()["detail"].lower()

    async def test_block_reason_is_shown_to_blocked_user(
        self, client: AsyncClient, regular_user, test_session
    ):
        regular_user.is_blocked = True
        regular_user.block_reason = "violated the rules"
        await test_session.commit()

        await login_as(client, regular_user)
        response = await client.get("/users/me")
        assert "violated the rules" in response.json()["detail"]
