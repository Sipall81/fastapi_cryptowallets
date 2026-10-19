from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from eth_account import Account
from fastapi import HTTPException
from sqlalchemy import select

from database import BalanceModel, BlockchainTransactionModel, WalletModel
from services.wallet_service import fernet, wallet_service
from tests.helpers import make_mock_response

EXTERNAL_ADDRESS = "0x" + "ab" * 20


def close(actual, expected, tol: str = "1e-9"):
    """SQLite хранит Numeric как float, поэтому суммы сравниваем с допуском."""
    assert abs(Decimal(actual) - Decimal(str(expected))) <= Decimal(tol), f"{actual} != {expected}"


def price_client(price):
    """Мок CoinGecko-клиента для /simple/price: {"bitcoin": {"usd": price}}."""
    client = AsyncMock()
    client.get.return_value = make_mock_response({"bitcoin": {"usd": price}})
    return client


async def get_balance_row(session, user_id: int, currency: str) -> BalanceModel:
    result = await session.execute(
        select(BalanceModel).where(
            BalanceModel.user_id == user_id, BalanceModel.currency == currency
        )
    )
    return result.scalar_one()


async def get_tx(session, tx_hash: str) -> BlockchainTransactionModel:
    result = await session.execute(
        select(BlockchainTransactionModel).where(BlockchainTransactionModel.tx_hash == tx_hash)
    )
    return result.scalar_one()


async def count_txs(session) -> int:
    result = await session.execute(select(BlockchainTransactionModel))
    return len(result.scalars().all())


class TestCreateWallet:

    async def test_creates_and_persists_wallet(self, test_session, regular_user):
        created = await wallet_service.create_wallet(regular_user.id, test_session)

        result = await test_session.execute(
            select(WalletModel).where(WalletModel.user_id == regular_user.id)
        )
        row = result.scalar_one()
        assert row.address == created.address
        assert created.id == row.id
        assert created.address.startswith("0x")
        assert len(created.address) == 42
        assert created.created_at is not None

    async def test_private_key_is_stored_encrypted(self, test_session, regular_user):
        created = await wallet_service.create_wallet(regular_user.id, test_session)

        result = await test_session.execute(
            select(WalletModel).where(WalletModel.id == created.id)
        )
        row = result.scalar_one()

        decrypted = fernet.decrypt(row.encrypted_private_key.encode()).decode()
        assert decrypted not in row.encrypted_private_key  # в БД не лежит открытым текстом
        # расшифрованный ключ действительно соответствует адресу кошелька
        assert Account.from_key("0x" + decrypted.removeprefix("0x")).address == row.address

    async def test_wallets_have_unique_addresses(self, test_session, regular_user, admin_user):
        first = await wallet_service.create_wallet(regular_user.id, test_session)
        second = await wallet_service.create_wallet(admin_user.id, test_session)
        assert first.address != second.address


class TestDeposit:

    async def test_first_deposit_creates_balance(self, test_session, regular_user):
        client = price_client(50000)

        result = await wallet_service.deposit(
            regular_user.id, "BTC", Decimal("2"), test_session, client
        )

        # символ BTC превращён в coingecko-id
        assert result.currency == "bitcoin"
        assert result.amount == Decimal("2")
        args, kwargs = client.get.await_args
        assert args[0] == "/simple/price"
        assert kwargs["params"] == {"vs_currencies": "usd", "ids": "bitcoin"}

        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        assert balance.amount == Decimal("2")
        assert balance.avg_purchase_price_usd == Decimal("50000")
        assert balance.first_purchase_date is not None
        assert balance.last_purchase_date is not None

    async def test_deposit_by_coingecko_id(self, test_session, regular_user):
        client = price_client(50000)
        result = await wallet_service.deposit(
            regular_user.id, "bitcoin", Decimal("1"), test_session, client
        )
        assert result.currency == "bitcoin"

    async def test_second_deposit_recalculates_average_price(self, test_session, regular_user):
        await wallet_service.deposit(
            regular_user.id, "BTC", Decimal("2"), test_session, price_client(50000)
        )
        await wallet_service.deposit(
            regular_user.id, "BTC", Decimal("2"), test_session, price_client(60000)
        )

        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        assert balance.amount == Decimal("4")
        # (2 * 50000 + 2 * 60000) / 4
        close(balance.avg_purchase_price_usd, 55000)

    async def test_deposit_without_known_price_keeps_average(
        self, test_session, regular_user, make_balance
    ):
        await make_balance(regular_user, "bitcoin", "1", "100")
        client = AsyncMock()
        client.get.return_value = make_mock_response({})  # CoinGecko не вернул цену

        await wallet_service.deposit(regular_user.id, "bitcoin", Decimal("1"), test_session, client)

        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        assert balance.amount == Decimal("2")
        assert balance.avg_purchase_price_usd == Decimal("100")

    async def test_new_balance_without_price(self, test_session, regular_user):
        client = AsyncMock()
        client.get.return_value = make_mock_response({})

        await wallet_service.deposit(regular_user.id, "bitcoin", Decimal("3"), test_session, client)

        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        assert balance.amount == Decimal("3")
        assert balance.avg_purchase_price_usd == Decimal("0")
        assert balance.first_purchase_date is None

    async def test_unknown_coin(self, test_session, regular_user):
        client = AsyncMock()
        client.get.return_value = make_mock_response({}, status_code=404)

        with pytest.raises(HTTPException) as exc:
            await wallet_service.deposit(
                regular_user.id, "no-such-coin", Decimal("1"), test_session, client
            )
        assert exc.value.status_code == 404
        assert "CoinGecko has no coin_id" in exc.value.detail

    async def test_unknown_coin_creates_no_balance(self, test_session, regular_user):
        client = AsyncMock()
        client.get.return_value = make_mock_response({}, status_code=404)

        with pytest.raises(HTTPException):
            await wallet_service.deposit(
                regular_user.id, "no-such-coin", Decimal("1"), test_session, client
            )
        assert await wallet_service.get_balance(regular_user.id, test_session) == {}


class TestSendTransaction:

    async def test_internal_transfer_moves_funds(
        self, test_session, regular_user, admin_user, make_wallet, make_balance
    ):
        sender_wallet = await make_wallet(regular_user)
        receiver_wallet = await make_wallet(admin_user)
        await make_balance(regular_user, "bitcoin", "1")
        fee = wallet_service.GAS_FEES["bitcoin"]

        result = await wallet_service.send_transaction(
            regular_user.id, "bitcoin", receiver_wallet.address, Decimal("0.5"), test_session
        )

        assert result["internal"] is True
        assert result["status"] == "pending"
        assert result["currency"] == "bitcoin"
        assert result["amount"] == "0.5"
        assert result["gas_fee"] == str(fee)
        assert result["from_address"] == sender_wallet.address
        assert result["to_address"] == receiver_wallet.address
        assert result["tx_hash"].startswith("0x")
        assert len(result["tx_hash"]) == 66

        sender = await get_balance_row(test_session, regular_user.id, "bitcoin")
        receiver = await get_balance_row(test_session, admin_user.id, "bitcoin")
        close(sender.amount, Decimal("1") - Decimal("0.5") - fee)
        close(receiver.amount, "0.5")

    async def test_internal_transfer_credits_existing_receiver_balance(
        self, test_session, regular_user, admin_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        receiver_wallet = await make_wallet(admin_user)
        await make_balance(regular_user, "bitcoin", "1")
        await make_balance(admin_user, "bitcoin", "2")

        await wallet_service.send_transaction(
            regular_user.id, "bitcoin", receiver_wallet.address, Decimal("0.5"), test_session
        )

        receiver = await get_balance_row(test_session, admin_user.id, "bitcoin")
        close(receiver.amount, "2.5")

    async def test_transaction_is_saved_as_pending(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        sender_wallet = await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")

        result = await wallet_service.send_transaction(
            regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
        )

        tx = await get_tx(test_session, result["tx_hash"])
        assert tx.status == "pending"
        assert tx.from_address == sender_wallet.address
        assert tx.to_address == EXTERNAL_ADDRESS
        assert tx.currency == "bitcoin"
        close(tx.amount, "0.5")
        close(tx.gas_fee, wallet_service.GAS_FEES["bitcoin"])

    async def test_external_transfer(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        fee = wallet_service.GAS_FEES["bitcoin"]

        result = await wallet_service.send_transaction(
            regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
        )

        assert result["internal"] is False
        sender = await get_balance_row(test_session, regular_user.id, "bitcoin")
        close(sender.amount, Decimal("1") - Decimal("0.5") - fee)

    async def test_insufficient_balance(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "0.1")

        with pytest.raises(ValueError, match="Insufficient balance"):
            await wallet_service.send_transaction(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
            )

        sender = await get_balance_row(test_session, regular_user.id, "bitcoin")
        close(sender.amount, "0.1")
        assert await count_txs(test_session) == 0

    async def test_balance_exactly_equal_to_amount_plus_fee_is_enough(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        # tether: комиссия 1.0; все числа точно представимы в двоичном виде
        await make_wallet(regular_user)
        await make_balance(regular_user, "tether", "2.5")

        result = await wallet_service.send_transaction(
            regular_user.id, "tether", EXTERNAL_ADDRESS, Decimal("1.5"), test_session
        )

        assert result["gas_fee"] == "1.0"
        sender = await get_balance_row(test_session, regular_user.id, "tether")
        close(sender.amount, 0)

    async def test_one_cent_over_the_balance_is_rejected(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "tether", "2.5")

        with pytest.raises(ValueError, match="Insufficient balance"):
            await wallet_service.send_transaction(
                regular_user.id, "tether", EXTERNAL_ADDRESS, Decimal("1.51"), test_session
            )

    async def test_unknown_currency_uses_default_fee(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "mystery-coin", "1")

        result = await wallet_service.send_transaction(
            regular_user.id, "mystery-coin", EXTERNAL_ADDRESS, Decimal("0.1"), test_session
        )
        assert result["gas_fee"] == "0.001"

    async def test_no_balance(self, test_session, regular_user, make_wallet):
        await make_wallet(regular_user)

        with pytest.raises(ValueError, match="Balance for bitcoin not found"):
            await wallet_service.send_transaction(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.1"), test_session
            )

    async def test_no_wallet(self, test_session, regular_user, make_balance):
        await make_balance(regular_user, "bitcoin", "1")

        with pytest.raises(ValueError, match="Wallet not found"):
            await wallet_service.send_transaction(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.1"), test_session
            )


class TestWithdraw:

    async def test_withdraw_success(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        sender_wallet = await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")

        result = await wallet_service.withdraw(
            regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
        )

        assert result["is_withdraw"] is True
        assert result["status"] == "pending"
        assert result["to_address"] == EXTERNAL_ADDRESS
        assert result["from_address"] == sender_wallet.address

        tx = await get_tx(test_session, result["tx_hash"])
        assert tx.is_internal is False
        assert tx.status == "pending"

        # баланс уменьшился на сумму и комиссию (какой бы ни была комиссия)
        fee = Decimal(result["gas_fee"])
        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        close(balance.amount, Decimal("1") - Decimal("0.5") - fee)

    async def test_withdraw_no_balance(self, test_session, regular_user, make_wallet):
        await make_wallet(regular_user)

        with pytest.raises(ValueError, match="balance for bitcoin not found"):
            await wallet_service.withdraw(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.1"), test_session
            )

    async def test_withdraw_no_wallet(self, test_session, regular_user, make_balance):
        await make_balance(regular_user, "bitcoin", "1")

        with pytest.raises(ValueError, match="Wallet not found"):
            await wallet_service.withdraw(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.1"), test_session
            )

    async def test_withdraw_insufficient_balance(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "0.1")

        with pytest.raises(ValueError, match="Insufficient balance"):
            await wallet_service.withdraw(
                regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
            )
        assert await count_txs(test_session) == 0


class TestCancelTransaction:

    async def send_external(self, session, user, amount="0.5"):
        return await wallet_service.send_transaction(
            user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal(amount), session
        )

    async def test_cancel_refunds_amount_and_fee(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await self.send_external(test_session, regular_user)
        fee = wallet_service.GAS_FEES["bitcoin"]

        result = await wallet_service.cancel_transaction(
            sent["tx_hash"], regular_user.id, test_session
        )

        assert result["info"] == "Transaction cancelled"
        assert result["tx_hash"] == sent["tx_hash"]
        close(Decimal(result["refunded"]), Decimal("0.5") + fee)

        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        close(balance.amount, 1)

    async def test_cancel_marks_transaction(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await self.send_external(test_session, regular_user)

        await wallet_service.cancel_transaction(sent["tx_hash"], regular_user.id, test_session)

        tx = await get_tx(test_session, sent["tx_hash"])
        assert tx.status == "cancelled"
        assert tx.cancelled_at is not None
        assert tx.note == "Cancelled by user"

    async def test_cannot_cancel_twice(
        self, test_session, regular_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await self.send_external(test_session, regular_user)

        await wallet_service.cancel_transaction(sent["tx_hash"], regular_user.id, test_session)
        with pytest.raises(ValueError, match="not found or not pending"):
            await wallet_service.cancel_transaction(sent["tx_hash"], regular_user.id, test_session)

        # деньги вернулись ровно один раз
        balance = await get_balance_row(test_session, regular_user.id, "bitcoin")
        close(balance.amount, 1)

    async def test_cannot_cancel_foreign_transaction(
        self, test_session, regular_user, admin_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_wallet(admin_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await self.send_external(test_session, regular_user)

        with pytest.raises(ValueError, match="You are not the sender"):
            await wallet_service.cancel_transaction(sent["tx_hash"], admin_user.id, test_session)

        tx = await get_tx(test_session, sent["tx_hash"])
        assert tx.status == "pending"

    async def test_user_without_wallet_cannot_cancel(
        self, test_session, regular_user, admin_user, make_wallet, make_balance
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await self.send_external(test_session, regular_user)

        with pytest.raises(ValueError, match="You are not the sender"):
            await wallet_service.cancel_transaction(sent["tx_hash"], admin_user.id, test_session)

    async def test_unknown_transaction(self, test_session, regular_user):
        with pytest.raises(ValueError, match="not found or not pending"):
            await wallet_service.cancel_transaction("0xunknown", regular_user.id, test_session)


class TestGetters:

    async def test_get_wallet_returns_none_when_missing(self, test_session, regular_user):
        assert await wallet_service.get_wallet(regular_user.id, test_session) is None

    async def test_get_wallet(self, test_session, regular_user, make_wallet):
        wallet = await make_wallet(regular_user)
        found = await wallet_service.get_wallet(regular_user.id, test_session)
        assert found.address == wallet.address

    async def test_get_balance_empty(self, test_session, regular_user):
        assert await wallet_service.get_balance(regular_user.id, test_session) == {}

    async def test_get_balance(self, test_session, regular_user, admin_user, make_balance):
        await make_balance(regular_user, "bitcoin", "1.5")
        await make_balance(regular_user, "ethereum", "10")
        await make_balance(admin_user, "bitcoin", "999")

        balances = await wallet_service.get_balance(regular_user.id, test_session)
        assert set(balances) == {"bitcoin", "ethereum"}
        assert balances["bitcoin"] == Decimal("1.5")
        assert balances["ethereum"] == Decimal("10")
