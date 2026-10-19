import base64
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient

from database import BalanceModel
from routers.coingecko import _calculate_coin_data
from tests.helpers import make_mock_response

UPDATED = "2026-10-18T12:00:00.000Z"


def market_item(coin_id="bitcoin", price=67000, change=2.5, window="24h"):
    """Элемент ответа CoinGecko /coins/markets для /crypto/top_coins."""
    return {
        "id": coin_id,
        "current_price": price,
        f"price_change_percentage_{window}_in_currency": change,
        "last_updated": UPDATED,
    }


def full_coin_item(**overrides):
    """Полный элемент ответа /coins/markets для /crypto/coin/{coin_id}."""
    item = {
        "id": "bitcoin",
        "current_price": 67000,
        "price_change_24h": 1500.5,
        "price_change_percentage_1h_in_currency": 0.1,
        "price_change_percentage_24h_in_currency": 2.5,
        "price_change_percentage_7d_in_currency": 5.0,
        "price_change_percentage_14d_in_currency": 7.5,
        "price_change_percentage_30d_in_currency": 10.0,
        "price_change_percentage_200d_in_currency": 50.0,
        "price_change_percentage_1y_in_currency": 100.0,
        "last_updated": UPDATED,
    }
    item.update(overrides)
    return item


def hourly_prices(count: int):
    """count почасовых точек графика: от (count-1) часов назад до сейчас."""
    now_ms = int(time.time() * 1000)
    return [[now_ms - (count - 1 - i) * 3_600_000, 67000 + i * 10] for i in range(count)]


class TestTopCoins:

    async def test_top_coins_success(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            market_item("bitcoin", 67000, 2.5),
            market_item("ethereum", 3500, -1.2),
        ]))

        response = await client.get("/crypto/top_coins")
        assert response.status_code == 200
        data = response.json()
        assert [c["currency"] for c in data] == ["bitcoin", "ethereum"]
        assert float(data[0]["current_price"]) == 67000
        assert float(data[0]["price_change_percentage"]) == pytest.approx(2.5)
        assert float(data[1]["price_change_percentage"]) == pytest.approx(-1.2)
        assert data[0]["last_updated"].startswith("2026-10-18T12:00:00")

    async def test_top_coins_default_request_params(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([market_item()]))

        await client.get("/crypto/top_coins")

        mock_coingecko.get.assert_awaited_once()
        args, kwargs = mock_coingecko.get.await_args
        assert args[0] == "/coins/markets"
        assert kwargs["params"] == {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 20,
            "page": 1,
            "price_change_percentage": "24h",
        }

    async def test_top_coins_other_period(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            market_item("bitcoin", 67000, 5.5, window="7d"),
        ]))

        response = await client.get(
            "/crypto/top_coins",
            params={"price_change_percentage": "7d", "per_page": 1},
        )
        assert response.status_code == 200
        assert float(response.json()[0]["price_change_percentage"]) == pytest.approx(5.5)
        assert mock_coingecko.get.await_args.kwargs["params"]["price_change_percentage"] == "7d"

    async def test_top_coins_result_is_cached(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([market_item()]))

        await client.get("/crypto/top_coins")

        override_redis.setex.assert_awaited_once()
        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == "cg:top:20:1:24h"
        assert ttl == 60

    async def test_top_coins_served_from_cache(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        override_redis.get.return_value = json.dumps([{
            "currency": "bitcoin",
            "current_price": 1.0,
            "price_change_percentage": "1.5",
            "last_updated": "2026-10-18T12:00:00Z",
        }])
        mock_coingecko.get = AsyncMock()

        response = await client.get("/crypto/top_coins")
        assert response.status_code == 200
        assert response.json()[0]["currency"] == "bitcoin"
        mock_coingecko.get.assert_not_awaited()

    async def test_top_coins_coingecko_error(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response(
            {"error": "rate limit"}, status_code=429
        ))
        response = await client.get("/crypto/top_coins")
        assert response.status_code == 429
        assert "CoinGecko API error" in response.json()["detail"]

    async def test_top_coins_coingecko_unreachable(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        response = await client.get("/crypto/top_coins")
        assert response.status_code == 503
        assert "temporarily unavailable" in response.json()["detail"]

    @pytest.mark.parametrize("params", [
        {"per_page": 0},
        {"per_page": 51},
        {"page": 0},
    ])
    async def test_top_coins_validation(self, client: AsyncClient, mock_coingecko, params):
        response = await client.get("/crypto/top_coins", params=params)
        assert response.status_code == 422


class TestCoinData:

    async def test_coin_success(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([full_coin_item()]))

        response = await client.get("/crypto/coin/bitcoin")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "bitcoin"
        assert data["current_price"] == 67000
        assert data["price_change_24h"] == 1500.5
        assert data["price_change_percentage_1h"] == 0.1
        assert data["price_change_percentage_24h"] == 2.5
        assert data["price_change_percentage_7d"] == 5.0
        assert data["price_change_percentage_1y"] == 100.0
        assert data["last_updated"].startswith("2026-10-18T12:00:00")

    async def test_coin_symbol_is_mapped_to_coingecko_id(
        self, client: AsyncClient, mock_coingecko
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([full_coin_item()]))

        await client.get("/crypto/coin/BTC")

        args, kwargs = mock_coingecko.get.await_args
        assert args[0] == "/coins/markets"
        assert kwargs["params"]["ids"] == "bitcoin"
        assert kwargs["params"]["vs_currency"] == "usd"

    async def test_coin_result_is_cached(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([full_coin_item()]))

        await client.get("/crypto/coin/bitcoin")

        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == "cg:coin:bitcoin"
        assert ttl == 120

    async def test_coin_served_from_cache(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        override_redis.get.return_value = json.dumps({"id": "bitcoin", "current_price": 1})
        mock_coingecko.get = AsyncMock()

        response = await client.get("/crypto/coin/bitcoin")
        assert response.status_code == 200
        assert response.json()["id"] == "bitcoin"
        mock_coingecko.get.assert_not_awaited()

    async def test_coin_not_found(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response(
            {"error": "not found"}, status_code=404
        ))
        response = await client.get("/crypto/coin/unknown")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    async def test_coin_upstream_error_status_is_forwarded(
        self, client: AsyncClient, mock_coingecko
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({}, status_code=502))
        response = await client.get("/crypto/coin/bitcoin")
        assert response.status_code == 502

    async def test_coin_coingecko_unreachable(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        response = await client.get("/crypto/coin/bitcoin")
        assert response.status_code == 503


class TestTrending:

    async def test_trending_filtered(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "coins": [
                {
                    "item": {
                        "id": "bitcoin",
                        "name": "Bitcoin",
                        "symbol": "btc",
                        "thumb": "https://...",
                        "market_cap_rank": 1,
                        "data": {
                            "price": 67000,
                            "price_change_percentage_24h": {"usd": 2.5},
                            "market_cap": "$1.3T",
                            "total_volume": "$45B",
                        },
                    }
                }
            ]
        }))
        response = await client.get("/crypto/trending")
        assert response.status_code == 200
        data = response.json()
        assert len(data["coins"]) == 1
        coin = data["coins"][0]
        assert coin == {
            "id": "bitcoin",
            "symbol": "BTC",  # .upper()
            "price_usd": 67000,
            "price_change_percentage_24h": 2.5,
            "market_cap": "$1.3T",
            "total_volume": "$45B",
        }
        mock_coingecko.get.assert_awaited_once()
        assert mock_coingecko.get.await_args.args[0] == "/search/trending"

    async def test_trending_item_without_market_data(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "coins": [{"item": {"id": "newcoin", "symbol": "new"}}]
        }))
        response = await client.get("/crypto/trending")
        coin = response.json()["coins"][0]
        assert coin["symbol"] == "NEW"
        assert coin["price_usd"] is None
        assert coin["price_change_percentage_24h"] is None

    async def test_trending_change_is_not_a_dict(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "coins": [{"item": {
                "id": "x", "symbol": "x",
                "data": {"price": 1, "price_change_percentage_24h": None},
            }}]
        }))
        response = await client.get("/crypto/trending")
        assert response.json()["coins"][0]["price_change_percentage_24h"] is None

    async def test_trending_empty(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({"coins": []}))
        response = await client.get("/crypto/trending")
        assert response.status_code == 200
        assert response.json() == {"coins": []}

    async def test_trending_result_is_cached(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({"coins": []}))

        await client.get("/crypto/trending")

        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == "cg:trending"
        assert ttl == 300

    async def test_trending_served_from_cache(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        override_redis.get.return_value = json.dumps({"coins": [{"id": "cached"}]})
        mock_coingecko.get = AsyncMock()

        response = await client.get("/crypto/trending")
        assert response.json() == {"coins": [{"id": "cached"}]}
        mock_coingecko.get.assert_not_awaited()


class TestChart:

    async def test_chart_generates_png(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "prices": hourly_prices(24),
        }))

        response = await client.get(
            "/crypto/coin/chart",
            params={"coin_id": "bitcoin", "hours": 24},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature

        args, kwargs = mock_coingecko.get.await_args
        assert args[0] == "/coins/bitcoin/market_chart"
        assert kwargs["params"] == {"vs_currency": "usd", "days": 1}

    async def test_chart_requests_enough_days_for_hours(
        self, client: AsyncClient, mock_coingecko
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "prices": hourly_prices(30),
        }))

        response = await client.get(
            "/crypto/coin/chart",
            params={"coin_id": "bitcoin", "hours": 25},
        )
        assert response.status_code == 200
        assert mock_coingecko.get.await_args.kwargs["params"]["days"] == 2  # ceil(25/24)

    async def test_chart_symbol_is_mapped_to_coingecko_id(
        self, client: AsyncClient, mock_coingecko
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "prices": hourly_prices(24),
        }))

        await client.get("/crypto/coin/chart", params={"coin_id": "BTC", "hours": 24})
        assert mock_coingecko.get.await_args.args[0] == "/coins/bitcoin/market_chart"

    async def test_chart_is_cached(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({
            "prices": hourly_prices(24),
        }))

        await client.get("/crypto/coin/chart", params={"coin_id": "bitcoin", "hours": 24})

        key, ttl, payload = override_redis.setex.await_args.args
        assert key == "cg:chart:bitcoin:24"
        assert ttl == 150
        assert base64.b64decode(payload)[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_chart_served_from_cache(
        self, client: AsyncClient, mock_coingecko, override_redis
    ):
        png = b"\x89PNG\r\n\x1a\n" + b"cached-image"
        override_redis.get.return_value = base64.b64encode(png).decode()
        mock_coingecko.get = AsyncMock()

        response = await client.get(
            "/crypto/coin/chart", params={"coin_id": "bitcoin", "hours": 24}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == png
        mock_coingecko.get.assert_not_awaited()

    async def test_chart_no_data_for_period(self, client: AsyncClient, mock_coingecko):
        old_point = [int(time.time() * 1000) - 100 * 3_600_000, 1.0]
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({"prices": [old_point]}))

        response = await client.get(
            "/crypto/coin/chart", params={"coin_id": "bitcoin", "hours": 24}
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "No data for this period"

    async def test_chart_unknown_coin(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({}, status_code=404))

        response = await client.get("/crypto/coin/chart", params={"coin_id": "nope"})
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    async def test_chart_upstream_error_status_is_forwarded(
        self, client: AsyncClient, mock_coingecko
    ):
        mock_coingecko.get = AsyncMock(return_value=make_mock_response({}, status_code=429))

        response = await client.get("/crypto/coin/chart", params={"coin_id": "bitcoin"})
        assert response.status_code == 429

    async def test_chart_coingecko_unreachable(self, client: AsyncClient, mock_coingecko):
        mock_coingecko.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        response = await client.get("/crypto/coin/chart", params={"coin_id": "bitcoin"})
        assert response.status_code == 503

    @pytest.mark.parametrize("params", [
        {"coin_id": "bitcoin", "hours": 0},
        {"coin_id": "bitcoin", "hours": 721},
        {"hours": 24},  # нет coin_id
    ])
    async def test_chart_validation(self, client: AsyncClient, mock_coingecko, params):
        response = await client.get("/crypto/coin/chart", params=params)
        assert response.status_code == 422


class TestCalculateCoinData:
    """Юнит-тесты расчёта прибыли (чистая функция, без БД и сети)."""

    @staticmethod
    def balance(amount, avg_price):
        return BalanceModel(
            user_id=1,
            currency="bitcoin",
            amount=Decimal(amount),
            avg_purchase_price_usd=Decimal(avg_price),
        )

    def test_profit(self):
        result = _calculate_coin_data(self.balance("2", "100"), Decimal("150"))
        assert result.currency == "bitcoin"
        assert Decimal(result.total_purchase_value_usd) == 200
        assert Decimal(result.total_current_value_usd) == 300
        assert Decimal(result.profit_usd) == 100
        assert result.percentage_change == "50.00"

    def test_loss(self):
        result = _calculate_coin_data(self.balance("2", "100"), Decimal("50"))
        assert Decimal(result.profit_usd) == -100
        assert result.percentage_change == "-50.00"

    def test_percentage_is_rounded_to_two_places(self):
        result = _calculate_coin_data(self.balance("1", "3"), Decimal("4"))
        assert result.percentage_change == "33.33"

    def test_zero_average_price_gives_zero_percent(self):
        # Монеты, полученные переводом, имеют avg_purchase_price_usd = 0
        result = _calculate_coin_data(self.balance("1", "0"), Decimal("100"))
        assert result.percentage_change == "0.00"
        assert Decimal(result.profit_usd) == 100

    def test_missing_price_counts_as_zero_value(self):
        result = _calculate_coin_data(self.balance("2", "100"), None)
        assert Decimal(result.total_current_value_usd) == 0
        assert Decimal(result.profit_usd) == -200
        assert result.percentage_change == "-100.00"

    def test_last_updated_is_passed_through(self):
        moment = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        result = _calculate_coin_data(self.balance("1", "1"), Decimal("1"), moment)
        assert result.last_updated_at == moment

    def test_last_updated_defaults_to_now(self):
        result = _calculate_coin_data(self.balance("1", "1"), Decimal("1"))
        delta = datetime.now(timezone.utc) - result.last_updated_at
        assert abs(delta.total_seconds()) < 5


class TestMyCoinChanges:

    async def test_all_changes_require_auth(self, client: AsyncClient, mock_coingecko):
        response = await client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 401

    async def test_all_changes_success(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "2", "100")
        await make_balance(regular_user, "ethereum", "1", "3000")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            {"id": "bitcoin", "current_price": 150, "last_updated": UPDATED},
            {"id": "ethereum", "current_price": 2500, "last_updated": UPDATED},
        ]))

        response = await auth_client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 200
        by_currency = {c["currency"]: c for c in response.json()}

        btc = by_currency["bitcoin"]
        assert Decimal(btc["amount"]) == 2
        assert Decimal(btc["profit_usd"]) == 100
        assert btc["percentage_change"] == "50.00"
        assert btc["last_updated_at"].startswith("2026-10-18T12:00:00")

        eth = by_currency["ethereum"]
        assert Decimal(eth["profit_usd"]) == -500
        assert eth["percentage_change"] == "-16.67"

    async def test_all_changes_request_contains_all_user_coins(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        await make_balance(regular_user, "ethereum", "1", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([]))

        await auth_client.get("/crypto/my_all_coins_changes")

        args, kwargs = mock_coingecko.get.await_args
        assert args[0] == "/coins/markets"
        assert set(kwargs["params"]["ids"].split(",")) == {"bitcoin", "ethereum"}

    async def test_all_changes_without_balances(self, auth_client: AsyncClient, mock_coingecko):
        response = await auth_client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 404
        assert response.json()["detail"] == "Balances not found"

    async def test_all_changes_coin_missing_in_market_data(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "2", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([]))

        response = await auth_client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 200
        coin = response.json()[0]
        assert Decimal(coin["total_current_value_usd"]) == 0
        assert coin["percentage_change"] == "-100.00"

    async def test_all_changes_coingecko_failure(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        mock_coingecko.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        response = await auth_client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 503
        assert "CoinGecko error" in response.json()["detail"]

    async def test_all_changes_result_is_cached(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko, override_redis
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            {"id": "bitcoin", "current_price": 150, "last_updated": UPDATED},
        ]))

        await auth_client.get("/crypto/my_all_coins_changes")

        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == f"cg:{regular_user.id}:all_coins_changes"
        assert ttl == 60

    async def test_all_changes_served_from_cache(
        self, auth_client: AsyncClient, mock_coingecko, override_redis
    ):
        override_redis.get.return_value = json.dumps([{
            "currency": "bitcoin",
            "amount": "1",
            "total_purchase_value_usd": "100",
            "total_current_value_usd": "150",
            "current_value_usd": "150",
            "profit_usd": "50",
            "percentage_change": "50.00",
            "last_updated_at": "2026-10-18T12:00:00Z",
        }])
        mock_coingecko.get = AsyncMock()

        response = await auth_client.get("/crypto/my_all_coins_changes")
        assert response.status_code == 200
        assert response.json()[0]["profit_usd"] == "50"
        mock_coingecko.get.assert_not_awaited()

    async def test_one_change_requires_auth(self, client: AsyncClient, mock_coingecko):
        response = await client.get("/crypto/see_coin_changes/bitcoin")
        assert response.status_code == 401

    async def test_one_change_success(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "2", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            {"id": "bitcoin", "current_price": 150, "last_updated": UPDATED},
        ]))

        response = await auth_client.get("/crypto/see_coin_changes/bitcoin")
        assert response.status_code == 200
        data = response.json()
        assert data["currency"] == "bitcoin"
        assert Decimal(data["profit_usd"]) == 100
        assert data["percentage_change"] == "50.00"
        assert mock_coingecko.get.await_args.kwargs["params"] == {
            "vs_currency": "usd",
            "ids": "bitcoin",
        }

    async def test_one_change_symbol_is_mapped(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            {"id": "bitcoin", "current_price": 150, "last_updated": UPDATED},
        ]))

        response = await auth_client.get("/crypto/see_coin_changes/BTC")
        assert response.status_code == 200
        assert response.json()["currency"] == "bitcoin"

    async def test_one_change_without_balance(self, auth_client: AsyncClient, mock_coingecko):
        response = await auth_client.get("/crypto/see_coin_changes/bitcoin")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
        mock_coingecko.get.assert_not_awaited()

    async def test_one_change_coingecko_failure(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        mock_coingecko.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        response = await auth_client.get("/crypto/see_coin_changes/bitcoin")
        assert response.status_code == 503

    async def test_one_change_result_is_cached(
        self, auth_client: AsyncClient, regular_user, make_balance, mock_coingecko, override_redis
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        mock_coingecko.get = AsyncMock(return_value=make_mock_response([
            {"id": "bitcoin", "current_price": 150, "last_updated": UPDATED},
        ]))

        await auth_client.get("/crypto/see_coin_changes/bitcoin")

        key, ttl, _payload = override_redis.setex.await_args.args
        assert key == f"cg:{regular_user.id}:coin_changes:bitcoin"
        assert ttl == 30

    async def test_one_change_served_from_cache(
        self, auth_client: AsyncClient, mock_coingecko, override_redis
    ):
        override_redis.get.return_value = json.dumps({
            "currency": "bitcoin",
            "amount": "1",
            "total_purchase_value_usd": "100",
            "total_current_value_usd": "150",
            "current_value_usd": "150",
            "profit_usd": "50",
            "percentage_change": "50.00",
            "last_updated_at": "2026-10-18T12:00:00Z",
        })
        mock_coingecko.get = AsyncMock()

        response = await auth_client.get("/crypto/see_coin_changes/bitcoin")
        assert response.status_code == 200
        assert response.json()["profit_usd"] == "50"
        mock_coingecko.get.assert_not_awaited()
