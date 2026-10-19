"""
Тесты на ПРАВИЛЬНОЕ поведение, которое сейчас не выполняется (найдено при чтении кода).

Все тесты помечены xfail: пока баг есть, они «ожидаемо падают» и не ломают прогон.
Когда баг исправлен, тест начнёт проходить (XPASS) — тогда уберите с него маркер xfail.

Список:
  1. Отмена внутреннего перевода возвращает деньги отправителю, но не списывает у получателя.
  2. Второй депозит с дробной ценой падает (float * Decimal).
  3. withdraw ищет комиссию по currency.upper(), а ключи GAS_FEES — в нижнем регистре.
  4. /users/me отдаёт hashed_password.
  5. /admin/users отдаёт hashed_password.
  6. /admin/change_user_data падает с KeyError, если не передан role.
  7. /users/refresh требует валидный access_token (а он как раз протухает).
  8. /users/refresh принимает access-токен в роли refresh-токена.
  9. SPECIAL_LIMITS в middleware никогда не срабатывают (ключи без ведущего "/").
 10. /crypto/coin/{id}: пустой список от CoinGecko -> IndexError вместо 404.
 11. /crypto/trending: ошибка CoinGecko не обрабатывается.
 12. Пароль длиннее 72 байт: bcrypt>=5 бросает ValueError -> 500 при регистрации.
"""
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from services.wallet_service import wallet_service
from tests.helpers import login_as, make_mock_response

EXTERNAL_ADDRESS = "0x" + "ab" * 20


def close(actual, expected, tol: str = "1e-9"):
    assert abs(Decimal(actual) - Decimal(str(expected))) <= Decimal(tol), f"{actual} != {expected}"


# ──────────────────────────────────────────────
# Деньги
# ──────────────────────────────────────────────

@pytest.mark.xfail(
    reason="cancel_transaction возвращает amount+fee отправителю, но не списывает "
           "сумму у получателя внутреннего перевода -> деньги дублируются",
)
async def test_cancel_internal_transfer_takes_money_back_from_receiver(
    test_session, regular_user, admin_user, make_wallet, make_balance
):
    await make_wallet(regular_user)
    receiver_wallet = await make_wallet(admin_user)
    await make_balance(regular_user, "bitcoin", "1")

    sent = await wallet_service.send_transaction(
        regular_user.id, "bitcoin", receiver_wallet.address, Decimal("0.5"), test_session
    )
    await wallet_service.cancel_transaction(sent["tx_hash"], regular_user.id, test_session)

    sender_balance = (await wallet_service.get_balance(regular_user.id, test_session))["bitcoin"]
    receiver_balance = (await wallet_service.get_balance(admin_user.id, test_session)).get(
        "bitcoin", Decimal(0)
    )
    close(sender_balance, 1)
    close(receiver_balance, 0)


@pytest.mark.xfail(
    reason="deposit: price_usd (float из JSON CoinGecko) * Decimal -> TypeError "
           "при втором пополнении той же монеты",
)
async def test_second_deposit_with_fractional_price(test_session, regular_user):
    def client_with_price(price):
        client = AsyncMock()
        client.get.return_value = make_mock_response({"bitcoin": {"usd": price}})
        return client

    await wallet_service.deposit(
        regular_user.id, "bitcoin", Decimal("1"), test_session, client_with_price(50000.5)
    )
    await wallet_service.deposit(
        regular_user.id, "bitcoin", Decimal("1"), test_session, client_with_price(50000.5)
    )

    balances = await wallet_service.get_balance(regular_user.id, test_session)
    assert balances["bitcoin"] == Decimal("2")


@pytest.mark.xfail(
    reason="withdraw использует GAS_FEES.get(currency.upper()), а ключи GAS_FEES "
           "в нижнем регистре -> всегда комиссия по умолчанию 0.001",
)
async def test_withdraw_uses_currency_specific_gas_fee(
    test_session, regular_user, make_wallet, make_balance
):
    await make_wallet(regular_user)
    await make_balance(regular_user, "bitcoin", "1")

    result = await wallet_service.withdraw(
        regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
    )
    assert Decimal(result["gas_fee"]) == wallet_service.GAS_FEES["bitcoin"]


# ──────────────────────────────────────────────
# Безопасность
# ──────────────────────────────────────────────

@pytest.mark.xfail(reason="/users/me возвращает ORM-объект целиком, включая hashed_password")
async def test_me_does_not_expose_password_hash(auth_client: AsyncClient):
    response = await auth_client.get("/users/me")
    assert response.status_code == 200
    assert "hashed_password" not in response.json()


@pytest.mark.xfail(reason="/admin/users возвращает ORM-объекты целиком, включая hashed_password")
async def test_admin_user_list_does_not_expose_password_hash(
    admin_client: AsyncClient, regular_user
):
    response = await admin_client.get("/admin/users")
    assert response.status_code == 200
    assert all("hashed_password" not in user for user in response.json())


@pytest.mark.xfail(
    reason="refresh_tokens зависит от CurrentUser: без живого access-токена обновить "
           "токены нельзя, хотя refresh нужен именно после истечения access",
)
async def test_refresh_works_without_valid_access_token(client: AsyncClient, regular_user):
    await login_as(client, regular_user)
    client.cookies.delete("access_token")  # access «протух»

    response = await client.post("/users/refresh")
    assert response.status_code == 200


@pytest.mark.xfail(reason="refresh_tokens не проверяет, что type токена == 'refresh'")
async def test_access_token_is_rejected_as_refresh_token(client: AsyncClient, regular_user):
    await login_as(client, regular_user)
    access_token = client.cookies.get("access_token")
    client.cookies.delete("refresh_token")
    client.cookies.set("refresh_token", access_token)

    response = await client.post("/users/refresh")
    assert response.status_code == 401


@pytest.mark.xfail(
    reason="bcrypt>=5 бросает ValueError для паролей > 72 байт, а модель допускает до 128 символов",
)
async def test_register_with_long_password_does_not_crash(client: AsyncClient):
    response = await client.post(
        "/users/register",
        params={"email": "long@example.com", "password": "p" * 100, "name": "Long"},
    )
    assert response.status_code in (200, 422)


# ──────────────────────────────────────────────
# Админка
# ──────────────────────────────────────────────

@pytest.mark.xfail(
    reason="change_user_data обращается к update_data['role'] без проверки наличия -> KeyError, "
           "если меняется только имя или email",
)
async def test_admin_can_change_only_the_name(
    admin_client: AsyncClient, regular_user, test_session
):
    response = await admin_client.patch(
        f"/admin/change_user_data/{regular_user.id}",
        params={"name": "Only Name"},
    )
    assert response.status_code == 200
    await test_session.refresh(regular_user)
    assert regular_user.name == "Only Name"


# ──────────────────────────────────────────────
# Rate limiter
# ──────────────────────────────────────────────

@pytest.mark.xfail(
    reason="SPECIAL_LIMITS содержит ключи 'users/login', а request.url.path = '/users/login' "
           "-> персональные лимиты никогда не применяются",
)
async def test_login_has_its_own_stricter_rate_limit(client: AsyncClient):
    response = await client.post(
        "/users/login", params={"email": "x@example.com", "password": "password123"}
    )
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert app.state.rate_limiter.is_limited.await_args.args[2:] == (5, 60)


# ──────────────────────────────────────────────
# CoinGecko
# ──────────────────────────────────────────────

@pytest.mark.xfail(
    reason="/coins/markets для неизвестного id отвечает 200 и [] -> response.json()[0] даёт "
           "IndexError (500) вместо 404",
)
async def test_unknown_coin_with_empty_upstream_list_returns_404(
    client: AsyncClient, mock_coingecko
):
    mock_coingecko.get = AsyncMock(return_value=make_mock_response([]))

    response = await client.get("/crypto/coin/no-such-coin")
    assert response.status_code == 404


@pytest.mark.xfail(reason="/crypto/trending не ловит HTTPStatusError от CoinGecko -> 500")
async def test_trending_forwards_upstream_error(client: AsyncClient, mock_coingecko):
    mock_coingecko.get = AsyncMock(return_value=make_mock_response({}, status_code=429))

    response = await client.get("/crypto/trending")
    assert response.status_code == 429
