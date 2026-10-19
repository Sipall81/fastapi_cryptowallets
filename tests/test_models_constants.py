from decimal import Decimal

import pytest
from pydantic import ValidationError

from constants import feesservice
from database import UserModel
from models import (
    AdminUserResponse,
    BalanceResponse,
    CancelRequest,
    SendRequest,
    SpecificCoin,
    UserLogin,
    UserResponse,
)


class TestRequestModels:

    def test_send_request_valid(self):
        req = SendRequest(currency="BTC", amount="0.1", to_address="0x1234567890")
        assert req.amount == Decimal("0.1")

    @pytest.mark.parametrize("amount", ["0", "-1"])
    def test_send_request_rejects_non_positive_amount(self, amount):
        with pytest.raises(ValidationError):
            SendRequest(currency="BTC", amount=amount, to_address="0x1234567890")

    def test_send_request_rejects_short_address(self):
        with pytest.raises(ValidationError):
            SendRequest(currency="BTC", amount="1", to_address="0x123")

    def test_send_request_rejects_short_currency(self):
        with pytest.raises(ValidationError):
            SendRequest(currency="B", amount="1", to_address="0x1234567890")

    def test_balance_response_requires_positive_amount(self):
        with pytest.raises(ValidationError):
            BalanceResponse(currency="BTC", amount="0")

    def test_cancel_request_requires_long_enough_hash(self):
        CancelRequest(tx_hash="0x" + "a" * 8)  # ровно 10 символов
        with pytest.raises(ValidationError):
            CancelRequest(tx_hash="0x123")

    def test_specific_coin_default_hours(self):
        assert SpecificCoin(coin_id="bitcoin").hours == 24

    @pytest.mark.parametrize("hours", [0, -1, 721])
    def test_specific_coin_rejects_invalid_hours(self, hours):
        with pytest.raises(ValidationError):
            SpecificCoin(coin_id="bitcoin", hours=hours)

    def test_specific_coin_accepts_max_hours(self):
        assert SpecificCoin(coin_id="bitcoin", hours=720).hours == 720

    def test_user_login_validation(self):
        UserLogin(email="a@b.com", password="12345678")
        with pytest.raises(ValidationError):
            UserLogin(email="not-an-email", password="12345678")
        with pytest.raises(ValidationError):
            UserLogin(email="a@b.com", password="1234567")


class TestResponseModels:

    def make_user(self):
        return UserModel(
            id=1,
            name="Name",
            email="a@b.com",
            hashed_password="secret-hash",
            role="user",
            is_blocked=False,
        )

    def test_user_response_hides_password_hash(self):
        data = UserResponse.model_validate(self.make_user()).model_dump()
        assert data == {"id": 1, "name": "Name", "email": "a@b.com"}

    def test_admin_user_response_has_admin_fields_but_no_password(self):
        data = AdminUserResponse.model_validate(self.make_user()).model_dump()
        assert data["role"] == "user"
        assert data["is_blocked"] is False
        assert data["blocked_at"] is None
        assert "hashed_password" not in data

    def test_to_dict_contains_all_columns(self):
        data = self.make_user().to_dict()
        assert set(data) == {
            "id", "name", "email", "hashed_password", "role",
            "is_blocked", "blocked_at", "block_reason",
        }


class TestConstants:

    def test_symbols_are_uppercase(self):
        assert all(symbol == symbol.upper() for symbol in feesservice.COINGECKO_IDS)

    def test_coingecko_ids_are_unique(self):
        ids = list(feesservice.COINGECKO_IDS.values())
        assert len(ids) == len(set(ids))

    def test_every_supported_coin_has_gas_fee(self):
        missing = [
            coin_id
            for coin_id in feesservice.COINGECKO_IDS.values()
            if coin_id not in feesservice.GAS_FEES
        ]
        assert missing == []

    def test_every_gas_fee_belongs_to_supported_coin(self):
        known = set(feesservice.COINGECKO_IDS.values())
        assert [c for c in feesservice.GAS_FEES if c not in known] == []

    def test_gas_fees_are_positive_decimals(self):
        for coin_id, fee in feesservice.GAS_FEES.items():
            assert isinstance(fee, Decimal), coin_id
            assert fee > 0, coin_id

    @pytest.mark.parametrize("symbol,coin_id", [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("USDT", "tether"),
        ("SOL", "solana"),
    ])
    def test_popular_symbols(self, symbol, coin_id):
        assert feesservice.COINGECKO_IDS[symbol] == coin_id
