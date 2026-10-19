import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from main import app
from rate_limiter import RateLimiter, get_rate_limiter, rate_limiter_factory


class TestRateLimiterMiddleware:

    async def test_rate_limit_blocks_after_threshold(self, client):
        call_count = {"n": 0}

        async def fake_is_limited(*args, **kwargs):
            call_count["n"] += 1
            return call_count["n"] > 3

        app.state.rate_limiter.is_limited = fake_is_limited

        for _ in range(3):
            response = await client.get("/users/")
            assert response.status_code == 200

        response = await client.get("/users/")
        assert response.status_code == 429

    async def test_blocked_response_has_retry_after_and_detail(self, client):
        app.state.rate_limiter.is_limited = AsyncMock(return_value=True)

        response = await client.get("/users/")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        assert "Too many requests" in response.json()["detail"]

    async def test_default_limit_is_20_per_minute(self, client):
        response = await client.get("/users/")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "20"

        limiter = app.state.rate_limiter.is_limited
        limiter.assert_awaited_once()
        _ip, endpoint, max_requests, window = limiter.await_args.args
        assert endpoint == "/users/"
        assert (max_requests, window) == (20, 60)

    async def test_blocked_request_does_not_reach_the_endpoint(
        self, client, regular_user, mock_rabbitmq
    ):
        app.state.rate_limiter.is_limited = AsyncMock(return_value=True)

        response = await client.post("/wallets/create")
        # 429, а не 401: лимитер срабатывает раньше авторизации
        assert response.status_code == 429
        mock_rabbitmq.assert_not_awaited()

    @pytest.mark.parametrize("path", ["/docs", "/redoc"])
    async def test_excluded_paths_are_not_limited(self, client, path):
        app.state.rate_limiter.is_limited = AsyncMock(return_value=True)

        response = await client.get(path)
        assert response.status_code == 200
        app.state.rate_limiter.is_limited.assert_not_awaited()


class FakePipeline:
    """Минимальная замена redis-pipeline: записывает команды и отдаёт заданный zcard."""

    def __init__(self, count: int):
        self.count = count
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def zremrangebyscore(self, *args, **kwargs):
        self.calls.append(("zremrangebyscore", args, kwargs))

    async def zadd(self, *args, **kwargs):
        self.calls.append(("zadd", args, kwargs))

    async def zcard(self, *args, **kwargs):
        self.calls.append(("zcard", args, kwargs))

    async def expire(self, *args, **kwargs):
        self.calls.append(("expire", args, kwargs))

    async def execute(self):
        return [0, 1, self.count, True]


class FakeRedis:
    def __init__(self, count: int):
        self.pipe = FakePipeline(count)

    def pipeline(self):
        return self.pipe


class TestRateLimiterClass:

    async def test_allows_requests_up_to_the_limit(self):
        limiter = RateLimiter(FakeRedis(count=3))
        assert await limiter.is_limited("1.2.3.4", "/x", 3, 60) is False

    async def test_blocks_requests_over_the_limit(self):
        limiter = RateLimiter(FakeRedis(count=4))
        assert await limiter.is_limited("1.2.3.4", "/x", 3, 60) is True

    async def test_uses_sliding_window_in_redis(self):
        fake = FakeRedis(count=1)
        limiter = RateLimiter(fake)
        before_ms = int(time.time() * 1000)

        await limiter.is_limited("1.2.3.4", "/x", 3, 60)

        names = [name for name, _args, _kwargs in fake.pipe.calls]
        assert names == ["zremrangebyscore", "zadd", "zcard", "expire"]

        calls = {name: (args, kwargs) for name, args, kwargs in fake.pipe.calls}
        key = "rl:/x:1.2.3.4"
        # старые записи за пределами окна 60 секунд удаляются
        remove_args, remove_kwargs = calls["zremrangebyscore"]
        assert remove_args == (key,)
        assert remove_kwargs["min"] == 0
        assert remove_kwargs["max"] == pytest.approx(before_ms - 60_000, abs=5_000)
        # текущий запрос записывается, ключ получает TTL = размеру окна
        assert calls["zadd"][0] == (key,)
        assert len(calls["zadd"][1]["mapping"]) == 1
        assert calls["zcard"][0] == (key,)
        assert calls["expire"][0] == (key, 60)

    async def test_key_depends_on_ip_and_endpoint(self):
        first, second = FakeRedis(count=1), FakeRedis(count=1)
        await RateLimiter(first).is_limited("1.1.1.1", "/a", 3, 60)
        await RateLimiter(second).is_limited("2.2.2.2", "/b", 3, 60)

        assert first.pipe.calls[0][1][0] == "rl:/a:1.1.1.1"
        assert second.pipe.calls[0][1][0] == "rl:/b:2.2.2.2"


class TestRateLimiterFactory:
    """rate_limiter_factory — зависимость для отдельных ручек (проверяем на мини-приложении)."""

    @staticmethod
    def build_app(limited: bool):
        fake = MagicMock()
        fake.is_limited = AsyncMock(return_value=limited)

        mini = FastAPI()

        @mini.get("/ping", dependencies=[Depends(rate_limiter_factory("ping", 2, 60))])
        async def ping():
            return {"ok": True}

        mini.dependency_overrides[get_rate_limiter] = lambda: fake
        return mini, fake

    async def test_allows_request_when_not_limited(self):
        mini, fake = self.build_app(limited=False)
        async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://test") as ac:
            response = await ac.get("/ping")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert fake.is_limited.await_args.args[1:] == ("ping", 2, 60)

    async def test_returns_429_when_limited(self):
        mini, _fake = self.build_app(limited=True)
        async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://test") as ac:
            response = await ac.get("/ping")

        assert response.status_code == 429
        assert "Too many requests" in response.json()["detail"]
