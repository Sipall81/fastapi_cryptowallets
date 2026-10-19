import json
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import BalanceModel, BlockchainTransactionModel, OperationModel, WalletModel
from services.wallet_service import wallet_service
from tests.helpers import make_mock_response
from workers import worker

EXTERNAL_ADDRESS = "0x" + "ab" * 20


class FakeMessage:
    """Заменяет aio_pika.IncomingMessage: body + async with message.process()."""

    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode()

    @asynccontextmanager
    async def process(self):
        yield


@pytest.fixture(autouse=True)
def worker_db(test_engine, monkeypatch):
    """Воркер открывает свои сессии через new_session — направляем их в тестовую БД."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(worker, "new_session", factory)


def parse_result(operation: OperationModel) -> dict:
    return json.loads(operation.result)


class TestProcessWalletCreate:

    async def test_creates_wallet_and_completes_operation(
        self, test_session, regular_user, make_operation
    ):
        op = await make_operation(regular_user, "wallet_create")

        await worker.process_wallet_create(
            FakeMessage({"operation_id": op.operation_id, "user_id": regular_user.id})
        )

        await test_session.refresh(op)
        assert op.status == "completed"
        result = parse_result(op)

        wallet = (
            await test_session.execute(
                select(WalletModel).where(WalletModel.user_id == regular_user.id)
            )
        ).scalar_one()
        assert result["address"] == wallet.address
        assert result["wallet_id"] == wallet.id


class TestProcessDeposit:

    async def test_deposit_updates_balance_and_operation(
        self, test_session, regular_user, make_operation, monkeypatch
    ):
        client = AsyncMock()
        client.get.return_value = make_mock_response({"bitcoin": {"usd": 50000}})
        monkeypatch.setattr(worker, "http_client", client)
        op = await make_operation(regular_user, "deposit")

        await worker.process_deposit(FakeMessage({
            "operation_id": op.operation_id,
            "user_id": regular_user.id,
            "currency": "BTC",
            "amount": "1",
        }))

        await test_session.refresh(op)
        assert op.status == "completed"
        result = parse_result(op)
        assert result["currency"] == "bitcoin"
        assert Decimal(result["amount"]) == 1

        balance = (
            await test_session.execute(
                select(BalanceModel).where(BalanceModel.user_id == regular_user.id)
            )
        ).scalar_one()
        assert balance.currency == "bitcoin"
        assert balance.amount == Decimal("1")

    async def test_unknown_coin_marks_operation_failed(
        self, test_session, regular_user, make_operation, monkeypatch
    ):
        client = AsyncMock()
        client.get.return_value = make_mock_response({}, status_code=404)
        monkeypatch.setattr(worker, "http_client", client)
        op = await make_operation(regular_user, "deposit")

        with pytest.raises(HTTPException):
            await worker.process_deposit(FakeMessage({
                "operation_id": op.operation_id,
                "user_id": regular_user.id,
                "currency": "NOPE",
                "amount": "1",
            }))

        await test_session.refresh(op)
        assert op.status == "failed"
        assert "CoinGecko has no coin_id" in parse_result(op)["error"]


class TestProcessSend:

    async def test_internal_send_completes_operation(
        self, test_session, regular_user, admin_user, make_wallet, make_balance, make_operation
    ):
        await make_wallet(regular_user)
        receiver = await make_wallet(admin_user)
        await make_balance(regular_user, "bitcoin", "1")
        op = await make_operation(regular_user, "send")

        await worker.process_send(FakeMessage({
            "operation_id": op.operation_id,
            "user_id": regular_user.id,
            "currency": "bitcoin",
            "amount": "0.5",
            "to_address": receiver.address,
        }))

        await test_session.refresh(op)
        assert op.status == "completed"
        result = parse_result(op)
        assert result["internal"] is True
        assert result["amount"] == "0.5"

        tx = (
            await test_session.execute(
                select(BlockchainTransactionModel).where(
                    BlockchainTransactionModel.tx_hash == result["tx_hash"]
                )
            )
        ).scalar_one()
        assert tx.to_address == receiver.address

    async def test_insufficient_funds_marks_operation_failed(
        self, test_session, regular_user, make_wallet, make_balance, make_operation
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "0.1")
        op = await make_operation(regular_user, "send")

        with pytest.raises(ValueError, match="Insufficient balance"):
            await worker.process_send(FakeMessage({
                "operation_id": op.operation_id,
                "user_id": regular_user.id,
                "currency": "bitcoin",
                "amount": "5",
                "to_address": EXTERNAL_ADDRESS,
            }))

        await test_session.refresh(op)
        assert op.status == "failed"
        assert "Insufficient balance" in parse_result(op)["error"]

    async def test_missing_balance_marks_operation_failed(
        self, test_session, regular_user, make_operation
    ):
        op = await make_operation(regular_user, "send")

        with pytest.raises(ValueError):
            await worker.process_send(FakeMessage({
                "operation_id": op.operation_id,
                "user_id": regular_user.id,
                "currency": "bitcoin",
                "amount": "1",
                "to_address": EXTERNAL_ADDRESS,
            }))

        await test_session.refresh(op)
        assert op.status == "failed"
        assert "Balance for bitcoin not found" in parse_result(op)["error"]


class TestProcessCancel:

    async def test_cancel_completes_operation(
        self, test_session, regular_user, make_wallet, make_balance, make_operation
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        sent = await wallet_service.send_transaction(
            regular_user.id, "bitcoin", EXTERNAL_ADDRESS, Decimal("0.5"), test_session
        )
        op = await make_operation(regular_user, "cancel")

        await worker.process_cancel(FakeMessage({
            "operation_id": op.operation_id,
            "user_id": regular_user.id,
            "tx_hash": sent["tx_hash"],
        }))

        await test_session.refresh(op)
        assert op.status == "completed"
        assert parse_result(op)["info"] == "Transaction cancelled"

        tx = (
            await test_session.execute(
                select(BlockchainTransactionModel).where(
                    BlockchainTransactionModel.tx_hash == sent["tx_hash"]
                )
            )
        ).scalar_one()
        await test_session.refresh(tx)
        assert tx.status == "cancelled"

    async def test_cancel_unknown_transaction_marks_operation_failed(
        self, test_session, regular_user, make_operation
    ):
        op = await make_operation(regular_user, "cancel")

        with pytest.raises(ValueError):
            await worker.process_cancel(FakeMessage({
                "operation_id": op.operation_id,
                "user_id": regular_user.id,
                "tx_hash": "0xunknown",
            }))

        await test_session.refresh(op)
        assert op.status == "failed"
        assert "not found or not pending" in parse_result(op)["error"]


class TestProcessWithdraw:

    async def test_withdraw_completes_operation(
        self, test_session, regular_user, make_wallet, make_balance, make_operation
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "1")
        op = await make_operation(regular_user, "withdraw_blocked")

        await worker.process_withdraw(FakeMessage({
            "operation_id": op.operation_id,
            "user_id": regular_user.id,
            "currency": "bitcoin",
            "to_address": EXTERNAL_ADDRESS,
            "amount": "0.5",
        }))

        await test_session.refresh(op)
        assert op.status == "completed"
        result = parse_result(op)
        assert result["is_withdraw"] is True
        assert result["to_address"] == EXTERNAL_ADDRESS

    async def test_withdraw_insufficient_funds_marks_operation_failed(
        self, test_session, regular_user, make_wallet, make_balance, make_operation
    ):
        await make_wallet(regular_user)
        await make_balance(regular_user, "bitcoin", "0.1")
        op = await make_operation(regular_user, "withdraw_blocked")

        with pytest.raises(ValueError, match="Insufficient balance"):
            await worker.process_withdraw(FakeMessage({
                "operation_id": op.operation_id,
                "user_id": regular_user.id,
                "currency": "bitcoin",
                "to_address": EXTERNAL_ADDRESS,
                "amount": "5",
            }))

        await test_session.refresh(op)
        assert op.status == "failed"


class TestUpdateOperation:

    async def test_unknown_operation_is_ignored(self, test_session):
        await worker.update_operation_failed("no-such-operation", "boom")

        result = await test_session.execute(select(OperationModel))
        assert result.scalars().all() == []

    async def test_update_operation_failed_sets_status_and_error(
        self, test_session, regular_user, make_operation
    ):
        op = await make_operation(regular_user)

        await worker.update_operation_failed(op.operation_id, "boom")

        await test_session.refresh(op)
        assert op.status == "failed"
        assert parse_result(op) == {"error": "boom"}
