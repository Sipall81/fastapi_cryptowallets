import json
import os
import uuid
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ВАЖНО: тестовые переменные окружения ДО импорта app.
# Переменные окружения приоритетнее .env, поэтому тесты не зависят от локального
# .env и никогда не используют реальные ключи (CoinGecko, JWT и т.д.).
os.environ["SECRET_KEY"] = "eDMrXeKgH5FqllU17V9tJAZsUqVwikHqEIx3TJgVLSY"
os.environ["ENCRYPTION_KEY"] = "XbM57ekzQfnT2VVRs4HvnFNmBn2iPOh4NeqtwvIpVAc="
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["RABBITMQ_URL"] = "amqp://guest:guest@localhost:5672/"
os.environ["COINGECKO_API_KEY"] = "test-api-key"
os.environ["COINGECKO_URL"] = "https://api.coingecko.test"
os.environ["ALGORITHM"] = "HS256"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"
os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "7"
# secure-куки не отправляются по http://test, поэтому для тестов отключаем
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_SAMESITE"] = "lax"

from auth.password import hash_password  # noqa: E402
from database import (  # noqa: E402
    Base,
    BalanceModel,
    OperationModel,
    UserModel,
    WalletModel,
    get_session,
)
from main import app  # noqa: E402
from tests.helpers import ADMIN_PASSWORD, REGULAR_PASSWORD, login_as  # noqa: E402


# ──────────────────────────────────────────────
# База данных in-memory
# ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    SessionLocal = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def override_get_session(test_session):
    async def _override():
        yield test_session

    app.dependency_overrides[get_session] = _override
    yield
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────
# HTTP-клиент
# ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(override_get_session) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.clear()  # гарантируем чистые куки
        yield ac


# ──────────────────────────────────────────────
# Пользователи
# ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def regular_user(test_session) -> UserModel:
    user = UserModel(
        name="Test User",
        email="test@example.com",
        hashed_password=hash_password(REGULAR_PASSWORD),
        role="user",
        is_blocked=False,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(test_session) -> UserModel:
    user = UserModel(
        name="Admin User",
        email="admin@example.com",
        hashed_password=hash_password(ADMIN_PASSWORD),
        role="admin",
        is_blocked=False,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_client(client, regular_user) -> AsyncClient:
    """Клиент, залогиненный как обычный пользователь."""
    await login_as(client, regular_user, REGULAR_PASSWORD)
    return client


@pytest_asyncio.fixture
async def admin_client(client, admin_user) -> AsyncClient:
    """
    Клиент, залогиненный как админ.

    Не используйте вместе с auth_client в одном тесте: это один и тот же
    объект client, и куки перезапишут друг друга.
    """
    await login_as(client, admin_user, ADMIN_PASSWORD)
    return client


# ──────────────────────────────────────────────
# Фабрики данных (кошельки, балансы, операции)
# ──────────────────────────────────────────────

@pytest.fixture
def make_wallet(test_session):
    async def _make(user, address: str | None = None) -> WalletModel:
        wallet = WalletModel(
            user_id=user.id,
            address=address or f"0x{user.id:040x}",
            encrypted_private_key="stub-encrypted-key",
        )
        test_session.add(wallet)
        await test_session.commit()
        await test_session.refresh(wallet)
        return wallet

    return _make


@pytest.fixture
def make_balance(test_session):
    async def _make(
        user,
        currency: str = "bitcoin",
        amount="1",
        avg_price="0",
    ) -> BalanceModel:
        balance = BalanceModel(
            user_id=user.id,
            currency=currency,
            amount=Decimal(str(amount)),
            avg_purchase_price_usd=Decimal(str(avg_price)),
        )
        test_session.add(balance)
        await test_session.commit()
        return balance

    return _make


@pytest.fixture
def make_operation(test_session):
    async def _make(
        user,
        op_type: str = "deposit",
        status: str = "pending",
        payload: dict | None = None,
        result: dict | None = None,
        operation_id: str | None = None,
    ) -> OperationModel:
        operation = OperationModel(
            operation_id=operation_id or uuid.uuid4().hex,
            user_id=user.id,
            type=op_type,
            status=status,
            payload=json.dumps(payload or {}),
            result=json.dumps(result) if result is not None else None,
        )
        test_session.add(operation)
        await test_session.commit()
        await test_session.refresh(operation)
        return operation

    return _make


# ──────────────────────────────────────────────
# Моки внешних сервисов (Redis, RabbitMQ, CoinGecko)
# ──────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def override_redis(monkeypatch):
    """
    Мок Redis: по умолчанию кэш пуст (get -> None), запись ничего не делает.
    В тестах можно менять: override_redis.get.return_value = "<json>".
    """
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.ping = AsyncMock(return_value=True)
    mock.aclose = AsyncMock()

    import sys

    for module_name in ["routers.users", "routers.coingecko", "routers.admin", "rate_limiter"]:
        if module_name in sys.modules:
            monkeypatch.setattr(f"{module_name}.redis_client", mock)

    monkeypatch.setattr("redis_client.redis_client", mock)
    yield mock


@pytest.fixture(autouse=True)
def mock_rabbitmq(monkeypatch):
    """Мок publish_message — сообщения не уходят в реальный RabbitMQ."""
    publish_mock = AsyncMock()
    monkeypatch.setattr("rabbitmq_client.publish_message", publish_mock)
    monkeypatch.setattr("routers.wallets.publish_message", publish_mock)
    return publish_mock


@pytest.fixture
def mock_coingecko():
    """Мок httpx-клиента для CoinGecko через dependency_overrides."""
    from auth.dependencies import get_http_client

    client_mock = AsyncMock()

    async def _override():
        return client_mock

    app.dependency_overrides[get_http_client] = _override
    yield client_mock
    app.dependency_overrides.pop(get_http_client, None)


@pytest_asyncio.fixture(autouse=True)
async def setup_app_state(monkeypatch):
    """Кладём в app.state мок rate_limiter, т.к. lifespan в тестах не выполняется."""
    mock_limiter = AsyncMock()
    mock_limiter.is_limited = AsyncMock(return_value=False)  # не блокируем
    app.state.rate_limiter = mock_limiter

    # Мок http_client для CoinGecko
    mock_http = AsyncMock()
    mock_http.get = AsyncMock()
    app.state.http_client = mock_http

    yield

    # Очистка
    if hasattr(app.state, "rate_limiter"):
        del app.state.rate_limiter
    if hasattr(app.state, "http_client"):
        del app.state.http_client
