import json

from httpx import AsyncClient

from tests.helpers import login_as


class TestGreeting:

    async def test_greeting(self, client: AsyncClient):
        response = await client.get("/users/")
        assert response.status_code == 200
        assert response.json() == "hi pall"


class TestUserProfile:

    async def test_get_user_by_id(self, auth_client: AsyncClient, regular_user):
        response = await auth_client.get(f"/users/user/{regular_user.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == regular_user.name
        assert data["email"] == regular_user.email

    async def test_get_user_does_not_expose_password_hash(
        self, auth_client: AsyncClient, regular_user
    ):
        response = await auth_client.get(f"/users/user/{regular_user.id}")
        assert set(response.json()) == {"id", "name", "email"}

    async def test_get_unknown_user(self, auth_client: AsyncClient):
        response = await auth_client.get("/users/user/99999")
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    async def test_get_user_requires_auth(self, client: AsyncClient, regular_user):
        response = await client.get(f"/users/user/{regular_user.id}")
        assert response.status_code == 401

    async def test_get_user_is_cached(
        self, auth_client: AsyncClient, regular_user, override_redis
    ):
        response = await auth_client.get(f"/users/user/{regular_user.id}")
        assert response.status_code == 200

        override_redis.setex.assert_awaited_once()
        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == f"from_user:user:{regular_user.id}"
        assert ttl == 300

    async def test_get_user_served_from_cache(
        self, auth_client: AsyncClient, override_redis
    ):
        # id 999 нет в БД — ответ может прийти только из кэша
        override_redis.get.return_value = json.dumps(
            {"id": 999, "name": "Cached", "email": "cached@example.com"}
        )

        response = await auth_client.get("/users/user/999")
        assert response.status_code == 200
        assert response.json()["name"] == "Cached"
        override_redis.setex.assert_not_awaited()


class TestAllUsers:

    async def test_all_users_requires_auth(self, client: AsyncClient):
        response = await client.get("/users/all_users")
        assert response.status_code == 401

    async def test_all_users_returns_only_public_fields(
        self, auth_client: AsyncClient, regular_user, admin_user
    ):
        response = await auth_client.get("/users/all_users")
        assert response.status_code == 200
        users = response.json()
        assert len(users) == 2
        assert all(set(u) == {"id", "name", "email"} for u in users)
        assert {u["email"] for u in users} == {regular_user.email, admin_user.email}

    async def test_all_users_is_cached(self, auth_client: AsyncClient, override_redis):
        await auth_client.get("/users/all_users")

        override_redis.setex.assert_awaited_once()
        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == "users:all"
        assert ttl == 300

    async def test_all_users_served_from_cache(
        self, auth_client: AsyncClient, override_redis
    ):
        override_redis.get.return_value = json.dumps(
            [{"id": 1, "name": "Cached", "email": "cached@example.com"}]
        )

        response = await auth_client.get("/users/all_users")
        assert response.status_code == 200
        assert response.json() == [
            {"id": 1, "name": "Cached", "email": "cached@example.com"}
        ]


class TestUpdateProfile:

    async def test_update_name(self, auth_client: AsyncClient, test_session, regular_user):
        response = await auth_client.patch(
            "/users/update_data",
            json={"name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

        await test_session.refresh(regular_user)
        assert regular_user.name == "Updated Name"

    async def test_update_email(self, auth_client: AsyncClient):
        response = await auth_client.patch(
            "/users/update_data",
            json={"email": "brand-new@example.com"},
        )
        assert response.status_code == 200
        assert response.json()["email"] == "brand-new@example.com"

    async def test_update_email_to_own_email_is_allowed(
        self, auth_client: AsyncClient, regular_user
    ):
        response = await auth_client.patch(
            "/users/update_data",
            json={"email": regular_user.email},
        )
        assert response.status_code == 200

    async def test_update_email_to_existing(
        self, auth_client: AsyncClient, admin_user
    ):
        response = await auth_client.patch(
            "/users/update_data",
            json={"email": admin_user.email},
        )
        assert response.status_code == 400
        assert "already in use" in response.json()["detail"]

    async def test_update_invalid_email(self, auth_client: AsyncClient):
        response = await auth_client.patch(
            "/users/update_data",
            json={"email": "not-an-email"},
        )
        assert response.status_code == 422

    async def test_update_with_empty_body_changes_nothing(
        self, auth_client: AsyncClient, regular_user
    ):
        response = await auth_client.patch("/users/update_data", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == regular_user.name
        assert data["email"] == regular_user.email

    async def test_update_requires_auth(self, client: AsyncClient):
        response = await client.patch("/users/update_data", json={"name": "X"})
        assert response.status_code == 401


class TestChangePassword:

    async def test_change_password_success(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        response = await client.post(
            "/users/me/change_password",
            json={"old_password": "password123", "new_password": "newpassword456"},
        )
        assert response.status_code == 200

        # Старый пароль больше не работает
        client.cookies.clear()
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "password123"},
        )
        assert response.status_code == 401

        # Новый работает
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "newpassword456"},
        )
        assert response.status_code == 200

    async def test_change_password_wrong_old(self, auth_client: AsyncClient):
        # Пароль валидной длины, но неверный -> 400 от самой ручки
        # (раньше тут было "wrong" — это отсекалось валидацией с 422 по другой причине)
        response = await auth_client.post(
            "/users/me/change_password",
            json={"old_password": "wrongpassword", "new_password": "newpassword456"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Old password is incorrect"

    async def test_change_password_new_too_short(self, auth_client: AsyncClient):
        response = await auth_client.post(
            "/users/me/change_password",
            json={"old_password": "password123", "new_password": "short"},
        )
        assert response.status_code == 422

    async def test_change_password_old_too_short(self, auth_client: AsyncClient):
        response = await auth_client.post(
            "/users/me/change_password",
            json={"old_password": "wrong", "new_password": "newpassword456"},
        )
        assert response.status_code == 422

    async def test_change_password_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/users/me/change_password",
            json={"old_password": "password123", "new_password": "newpassword456"},
        )
        assert response.status_code == 401
