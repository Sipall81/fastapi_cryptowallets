from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import create_access_token
from auth.password import verify_password
from config import settings
from database import UserModel
from tests.helpers import login_as


async def register(
    client: AsyncClient,
    email: str = "new@example.com",
    password: str = "password123",
    name: str = "New User",
):
    return await client.post(
        "/users/register",
        params={"email": email, "password": password, "name": name},
    )


class TestRegister:

    async def test_register_success(self, client: AsyncClient, test_session: AsyncSession):
        response = await register(client)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "new@example.com"
        assert data["name"] == "New User"
        assert "id" in data
        assert "password" not in data
        assert "hashed_password" not in data

        # Проверяем, что пользователь в БД
        result = await test_session.execute(
            select(UserModel).where(UserModel.email == "new@example.com")
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.role in ("user", "admin")

    async def test_first_registered_user_becomes_admin(
        self, client: AsyncClient, test_session: AsyncSession
    ):
        await register(client, email="first@example.com")
        await register(client, email="second@example.com")

        result = await test_session.execute(select(UserModel).order_by(UserModel.id))
        roles = [u.role for u in result.scalars().all()]
        assert roles == ["admin", "user"]

    async def test_register_stores_hashed_password(
        self, client: AsyncClient, test_session: AsyncSession
    ):
        await register(client, password="password123")

        result = await test_session.execute(
            select(UserModel).where(UserModel.email == "new@example.com")
        )
        user = result.scalar_one()
        assert user.hashed_password != "password123"
        assert verify_password("password123", user.hashed_password)

    async def test_register_duplicate_email(self, client: AsyncClient, regular_user):
        response = await register(client, email=regular_user.email, name="Duplicate")
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    async def test_register_invalid_email(self, client: AsyncClient):
        response = await register(client, email="not-an-email", name="X")
        assert response.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        response = await register(client, email="a@b.com", password="123", name="X")
        assert response.status_code == 422

    async def test_register_too_long_password(self, client: AsyncClient):
        response = await register(client, password="p" * 129)
        assert response.status_code == 422

    async def test_register_requires_name(self, client: AsyncClient):
        response = await client.post(
            "/users/register",
            params={"email": "a@b.com", "password": "password123"},
        )
        assert response.status_code == 422


class TestLogin:

    async def test_login_success(self, client: AsyncClient, regular_user):
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "password123"},
        )
        assert response.status_code == 200
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies
        assert response.cookies["access_token"]
        assert regular_user.name in response.json()["info"]

    async def test_login_cookies_are_httponly(self, client: AsyncClient, regular_user):
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "password123"},
        )
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert len(set_cookie_headers) == 2
        assert all("httponly" in header.lower() for header in set_cookie_headers)

    async def test_login_wrong_password(self, client: AsyncClient, regular_user):
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert "access_token" not in response.cookies

    async def test_login_unknown_email(self, client: AsyncClient):
        response = await client.post(
            "/users/login",
            params={"email": "unknown@example.com", "password": "password123"},
        )
        assert response.status_code == 401

    async def test_login_does_not_reveal_whether_email_exists(
        self, client: AsyncClient, regular_user
    ):
        wrong_password = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "wrong-password"},
        )
        unknown_email = await client.post(
            "/users/login",
            params={"email": "unknown@example.com", "password": "password123"},
        )
        assert wrong_password.json()["detail"] == unknown_email.json()["detail"]

    async def test_login_short_password_rejected_by_validation(
        self, client: AsyncClient, regular_user
    ):
        response = await client.post(
            "/users/login",
            params={"email": regular_user.email, "password": "123"},
        )
        assert response.status_code == 422


class TestLogout:

    async def test_logout_clears_cookies(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        assert "access_token" in client.cookies

        response = await client.post("/users/logout")
        assert response.status_code == 200
        assert "Logged out" in response.json()["info"]
        assert "access_token" not in response.cookies

    async def test_after_logout_protected_routes_are_closed(
        self, client: AsyncClient, regular_user
    ):
        await login_as(client, regular_user)
        await client.post("/users/logout")

        response = await client.get("/users/me")
        assert response.status_code == 401

    async def test_logout_without_login_is_ok(self, client: AsyncClient):
        response = await client.post("/users/logout")
        assert response.status_code == 200


class TestRefresh:

    async def test_refresh_issues_new_tokens(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)

        response = await client.post("/users/refresh")
        assert response.status_code == 200
        assert response.json()["info"] == "tokens refreshed"
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies

    async def test_refresh_requires_refresh_cookie(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        client.cookies.delete("refresh_token")

        response = await client.post("/users/refresh")
        assert response.status_code == 401
        assert response.json()["detail"] == "No refresh token"


class TestProtectedRoutes:

    async def test_me_requires_auth(self, client: AsyncClient):
        response = await client.get("/users/me")
        assert response.status_code == 401

    async def test_me_with_auth(self, client: AsyncClient, regular_user):
        await login_as(client, regular_user)
        response = await client.get("/users/me")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == regular_user.email
        assert data["role"] == "user"


class TestTokenValidation:
    """Проверка того, как зависимость get_current_user_base относится к плохим токенам."""

    async def test_garbage_token_rejected(self, client: AsyncClient):
        client.cookies.set("access_token", "not-a-jwt")
        response = await client.get("/users/me")
        assert response.status_code == 401
        assert "Invalid token" in response.json()["detail"]

    async def test_refresh_token_cannot_be_used_as_access_token(
        self, client: AsyncClient, regular_user
    ):
        await login_as(client, regular_user)
        refresh_token = client.cookies.get("refresh_token")

        client.cookies.clear()
        client.cookies.set("access_token", refresh_token)

        response = await client.get("/users/me")
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token type"

    async def test_expired_token_rejected(self, client: AsyncClient, regular_user):
        expired = jwt.encode(
            {
                "sub": str(regular_user.id),
                "type": "access",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        client.cookies.set("access_token", expired)

        response = await client.get("/users/me")
        assert response.status_code == 401

    async def test_token_signed_with_wrong_key_rejected(
        self, client: AsyncClient, regular_user
    ):
        forged = jwt.encode(
            {
                "sub": str(regular_user.id),
                "type": "access",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            "some-other-secret",
            algorithm=settings.algorithm,
        )
        client.cookies.set("access_token", forged)

        response = await client.get("/users/me")
        assert response.status_code == 401

    async def test_valid_token_of_deleted_user_returns_404(self, client: AsyncClient):
        client.cookies.set("access_token", create_access_token(user_id=99999))

        response = await client.get("/users/me")
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"
