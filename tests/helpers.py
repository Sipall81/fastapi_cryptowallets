import json
from unittest.mock import MagicMock

from httpx import AsyncClient, HTTPStatusError

# Пароли пользователей из фикстур regular_user / admin_user (см. conftest.py)
REGULAR_PASSWORD = "password123"
ADMIN_PASSWORD = "admin1234"


def make_mock_response(json_data, status_code: int = 200) -> MagicMock:
    """
    Создаёт мок httpx.Response для тестов.

    ВАЖНО: .json() и .raise_for_status() — СИНХРОННЫЕ методы,
    поэтому используются MagicMock, а не AsyncMock.
    """
    mock = MagicMock()
    mock.status_code = status_code
    mock.json = MagicMock(return_value=json_data)
    # Код роутеров подставляет response.text в detail ошибки — делаем его строкой
    mock.text = json.dumps(json_data, default=str)
    mock.raise_for_status = MagicMock()

    if status_code >= 400:
        mock.raise_for_status.side_effect = HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=mock,
        )

    return mock


async def login_as(client: AsyncClient, user, password: str | None = None):
    """Логинит пользователя через API (куки сохраняются в client) и проверяет успех."""
    if password is None:
        password = ADMIN_PASSWORD if user.role == "admin" else REGULAR_PASSWORD
    response = await client.post(
        "/users/login",
        params={"email": user.email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response
