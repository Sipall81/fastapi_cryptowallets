import json
from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from database import BlockchainTransactionModel, OperationModel

ADDRESS = "0x1234567890abcdef"


async def get_operation_row(session, operation_id: str) -> OperationModel:
    result = await session.execute(
        select(OperationModel).where(OperationModel.operation_id == operation_id)
    )
    return result.scalar_one()


class TestWalletCreate:

    async def test_create_wallet_requires_auth(self, client: AsyncClient):
        response = await client.post("/wallets/create")
        assert response.status_code == 401

    async def test_create_wallet_returns_operation(
        self, auth_client: AsyncClient, regular_user, mock_rabbitmq, test_session
    ):
        response = await auth_client.post("/wallets/create")
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"
        assert data["type"] == "wallet_create"

        # Сообщение ушло в очередь воркера
        mock_rabbitmq.assert_awaited_once()
        kwargs = mock_rabbitmq.await_args.kwargs
        assert kwargs["queue_name"] == "wallet.create"
        assert kwargs["message"] == {
            "operation_id": data["operation_id"],
            "user_id": regular_user.id,
        }

        # Операция сохранена в БД
        operation = await get_operation_row(test_session, data["operation_id"])
        assert operation.type == "wallet_create"
        assert operation.status == "pending"
        assert operation.user_id == regular_user.id

    async def test_create_wallet_when_wallet_exists(
        self, auth_client: AsyncClient, regular_user, make_wallet, mock_rabbitmq
    ):
        await make_wallet(regular_user)

        response = await auth_client.post("/wallets/create")
        assert response.status_code == 400
        assert response.json()["detail"] == "User already has a wallet"
        mock_rabbitmq.assert_not_awaited()

    async def test_blocked_user_cannot_create_wallet(
        self, auth_client: AsyncClient, regular_user, test_session, mock_rabbitmq
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await auth_client.post("/wallets/create")
        assert response.status_code == 403
        mock_rabbitmq.assert_not_awaited()


class TestDeposit:

    async def test_deposit_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/wallets/deposit", params={"currency": "BTC", "amount": "0.5"}
        )
        assert response.status_code == 401

    async def test_deposit_returns_operation(
        self, auth_client: AsyncClient, mock_rabbitmq
    ):
        response = await auth_client.post(
            "/wallets/deposit",
            params={"currency": "BTC", "amount": "0.5"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["type"] == "deposit"
        assert data["status"] == "pending"

    async def test_deposit_publishes_uppercased_currency(
        self, auth_client: AsyncClient, regular_user, mock_rabbitmq
    ):
        response = await auth_client.post(
            "/wallets/deposit",
            params={"currency": "btc", "amount": "0.5"},
        )
        message = mock_rabbitmq.await_args.kwargs["message"]
        assert mock_rabbitmq.await_args.kwargs["queue_name"] == "wallet.deposit"
        assert message["operation_id"] == response.json()["operation_id"]
        assert message["user_id"] == regular_user.id
        assert message["currency"] == "BTC"
        assert message["amount"] == "0.5"

    async def test_deposit_saves_operation(
        self, auth_client: AsyncClient, test_session
    ):
        response = await auth_client.post(
            "/wallets/deposit",
            params={"currency": "BTC", "amount": "0.5"},
        )
        operation = await get_operation_row(test_session, response.json()["operation_id"])
        payload = json.loads(operation.payload)
        assert operation.type == "deposit"
        assert payload["currency"] == "BTC"
        assert payload["amount"] == "0.5"

    async def test_deposit_rejects_non_positive_amount(
        self, auth_client: AsyncClient, mock_rabbitmq
    ):
        for amount in ("0", "-1"):
            response = await auth_client.post(
                "/wallets/deposit",
                params={"currency": "BTC", "amount": amount},
            )
            assert response.status_code == 422
        mock_rabbitmq.assert_not_awaited()

    async def test_deposit_rejects_too_short_currency(self, auth_client: AsyncClient):
        response = await auth_client.post(
            "/wallets/deposit",
            params={"currency": "B", "amount": "1"},
        )
        assert response.status_code == 422

    async def test_deposit_requires_amount(self, auth_client: AsyncClient):
        response = await auth_client.post(
            "/wallets/deposit", params={"currency": "BTC"}
        )
        assert response.status_code == 422


class TestSend:

    async def test_send_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/wallets/send",
            params={
                "currency": "BTC",
                "amount": "0.1",
                "to_address": ADDRESS,
            },
        )
        assert response.status_code == 401

    async def test_send_returns_operation(self, auth_client: AsyncClient, mock_rabbitmq):
        response = await auth_client.post(
            "/wallets/send",
            params={
                "currency": "BTC",
                "amount": "0.1",
                "to_address": ADDRESS,
            },
        )
        assert response.status_code == 202
        assert response.json()["type"] == "send"

    async def test_send_maps_symbol_to_coingecko_id(
        self, auth_client: AsyncClient, regular_user, mock_rabbitmq
    ):
        response = await auth_client.post(
            "/wallets/send",
            params={"currency": "BTC", "amount": "0.1", "to_address": ADDRESS},
        )
        kwargs = mock_rabbitmq.await_args.kwargs
        assert kwargs["queue_name"] == "wallet.send"
        assert kwargs["message"] == {
            "operation_id": response.json()["operation_id"],
            "user_id": regular_user.id,
            "currency": "bitcoin",
            "amount": "0.1",
            "to_address": ADDRESS,
        }

    async def test_send_keeps_coingecko_id_as_is(
        self, auth_client: AsyncClient, mock_rabbitmq
    ):
        await auth_client.post(
            "/wallets/send",
            params={"currency": "ethereum", "amount": "1", "to_address": ADDRESS},
        )
        assert mock_rabbitmq.await_args.kwargs["message"]["currency"] == "ethereum"

    async def test_send_saves_operation(self, auth_client: AsyncClient, test_session):
        response = await auth_client.post(
            "/wallets/send",
            params={"currency": "BTC", "amount": "0.1", "to_address": ADDRESS},
        )
        operation = await get_operation_row(test_session, response.json()["operation_id"])
        assert operation.type == "send"
        assert operation.status == "pending"
        assert json.loads(operation.payload)["to_address"] == ADDRESS

    async def test_send_validation(self, auth_client: AsyncClient, mock_rabbitmq):
        bad_params = [
            {"currency": "BTC", "amount": "0", "to_address": ADDRESS},
            {"currency": "BTC", "amount": "-5", "to_address": ADDRESS},
            {"currency": "BTC", "amount": "1", "to_address": "0x123"},  # адрес < 10
            {"currency": "B", "amount": "1", "to_address": ADDRESS},
            {"amount": "1", "to_address": ADDRESS},
        ]
        for params in bad_params:
            response = await auth_client.post("/wallets/send", params=params)
            assert response.status_code == 422, params
        mock_rabbitmq.assert_not_awaited()

    async def test_blocked_user_cannot_send(
        self, auth_client: AsyncClient, regular_user, test_session, mock_rabbitmq
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await auth_client.post(
            "/wallets/send",
            params={"currency": "BTC", "amount": "0.1", "to_address": ADDRESS},
        )
        assert response.status_code == 403
        assert "blocked" in response.json()["detail"].lower()
        mock_rabbitmq.assert_not_awaited()


class TestWithdraw:

    async def test_withdraw_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/wallets/withdraw",
            params={"currency": "BTC", "amount": "0.1", "to_address": ADDRESS},
        )
        assert response.status_code == 401

    async def test_blocked_user_can_withdraw(
        self, auth_client: AsyncClient, regular_user, test_session, mock_rabbitmq
    ):
        regular_user.is_blocked = True
        await test_session.commit()

        response = await auth_client.post(
            "/wallets/withdraw",
            params={"currency": "BTC", "amount": "0.1", "to_address": ADDRESS},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["type"] == "withdraw_blocked"
        assert data["status"] == "pending"

        kwargs = mock_rabbitmq.await_args.kwargs
        assert kwargs["queue_name"] == "wallet.withdraw"
        assert kwargs["message"]["currency"] == "bitcoin"
        assert kwargs["message"]["to_address"] == ADDRESS
        assert kwargs["message"]["amount"] == "0.1"

    async def test_withdraw_to_internal_address_is_rejected(
        self, auth_client: AsyncClient, admin_user, make_wallet, mock_rabbitmq
    ):
        internal = await make_wallet(admin_user)

        response = await auth_client.post(
            "/wallets/withdraw",
            params={"currency": "BTC", "amount": "0.1", "to_address": internal.address},
        )
        assert response.status_code == 400
        assert "internal address" in response.json()["detail"]
        mock_rabbitmq.assert_not_awaited()

    async def test_withdraw_validation(self, auth_client: AsyncClient):
        response = await auth_client.post(
            "/wallets/withdraw",
            params={"currency": "BTC", "amount": "0", "to_address": ADDRESS},
        )
        assert response.status_code == 422


class TestCancelTransaction:
    TX_HASH = "0x" + "ab" * 32

    async def test_cancel_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/wallets/cancel_transaction", json={"tx_hash": self.TX_HASH}
        )
        assert response.status_code == 401

    async def test_cancel_returns_operation(
        self, auth_client: AsyncClient, regular_user, mock_rabbitmq
    ):
        response = await auth_client.post(
            "/wallets/cancel_transaction", json={"tx_hash": self.TX_HASH}
        )
        assert response.status_code == 202
        data = response.json()
        assert data["type"] == "cancel"
        assert data["status"] == "pending"

        kwargs = mock_rabbitmq.await_args.kwargs
        assert kwargs["queue_name"] == "wallet.cancel"
        assert kwargs["message"] == {
            "operation_id": data["operation_id"],
            "user_id": regular_user.id,
            "tx_hash": self.TX_HASH,
        }

    async def test_cancel_rejects_short_tx_hash(
        self, auth_client: AsyncClient, mock_rabbitmq
    ):
        response = await auth_client.post(
            "/wallets/cancel_transaction", json={"tx_hash": "0x123"}
        )
        assert response.status_code == 422
        mock_rabbitmq.assert_not_awaited()


class TestOperations:

    async def test_get_operation(
        self, auth_client: AsyncClient, regular_user, make_operation
    ):
        operation = await make_operation(
            regular_user,
            op_type="send",
            status="completed",
            result={"tx_hash": "0xabc"},
        )
        response = await auth_client.get(f"/wallets/operations/{operation.operation_id}")
        assert response.status_code == 200
        assert response.json() == {
            "operation_id": operation.operation_id,
            "status": "completed",
            "type": "send",
            "result": {"tx_hash": "0xabc"},
        }

    async def test_get_pending_operation_has_no_result(
        self, auth_client: AsyncClient, regular_user, make_operation
    ):
        operation = await make_operation(regular_user)
        response = await auth_client.get(f"/wallets/operations/{operation.operation_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        assert response.json()["result"] is None

    async def test_cannot_read_foreign_operation(
        self, auth_client: AsyncClient, admin_user, make_operation
    ):
        foreign = await make_operation(admin_user)
        response = await auth_client.get(f"/wallets/operations/{foreign.operation_id}")
        assert response.status_code == 404

    async def test_unknown_operation(self, auth_client: AsyncClient):
        response = await auth_client.get("/wallets/operations/does-not-exist")
        assert response.status_code == 404

    async def test_operation_requires_auth(self, client: AsyncClient):
        response = await client.get("/wallets/operations/anything")
        assert response.status_code == 401

    async def test_my_operations_lists_only_own(
        self, auth_client: AsyncClient, regular_user, admin_user, make_operation
    ):
        mine_1 = await make_operation(regular_user)
        mine_2 = await make_operation(regular_user, op_type="send")
        await make_operation(admin_user)

        response = await auth_client.get("/wallets/operations")
        assert response.status_code == 200
        ids = {op["operation_id"] for op in response.json()}
        assert ids == {mine_1.operation_id, mine_2.operation_id}

    async def test_my_operations_limit(
        self, auth_client: AsyncClient, regular_user, make_operation
    ):
        for _ in range(3):
            await make_operation(regular_user)

        response = await auth_client.get("/wallets/operations", params={"limit": 2})
        assert response.status_code == 200
        assert len(response.json()) == 2


class TestTransactions:

    @staticmethod
    def make_tx(session, tx_hash, from_address, to_address, created_at):
        tx = BlockchainTransactionModel(
            tx_hash=tx_hash,
            from_address=from_address,
            to_address=to_address,
            currency="bitcoin",
            amount=Decimal("0.5"),
            created_at=created_at,
        )
        session.add(tx)
        return tx

    async def test_transactions_require_wallet(self, auth_client: AsyncClient):
        response = await auth_client.get("/wallets/transactions")
        assert response.status_code == 404
        assert response.json()["detail"] == "Wallet not found"

    async def test_transactions_require_auth(self, client: AsyncClient):
        response = await client.get("/wallets/transactions")
        assert response.status_code == 401

    async def test_lists_sent_and_received_newest_first(
        self, auth_client: AsyncClient, regular_user, admin_user, make_wallet, test_session
    ):
        mine = await make_wallet(regular_user)
        other = await make_wallet(admin_user)
        outsider = "0x" + "cd" * 20

        self.make_tx(test_session, "0xsent" + "0" * 10, mine.address, other.address, datetime(2026, 10, 1))
        self.make_tx(test_session, "0xrecv" + "0" * 10, other.address, mine.address, datetime(2026, 10, 2))
        self.make_tx(test_session, "0xfore" + "0" * 10, other.address, outsider, datetime(2026, 10, 3))
        await test_session.commit()

        response = await auth_client.get("/wallets/transactions")
        assert response.status_code == 200
        hashes = [tx["tx_hash"] for tx in response.json()]
        # чужая транзакция не видна, порядок — от новой к старой
        assert hashes == ["0xrecv" + "0" * 10, "0xsent" + "0" * 10]

    async def test_transactions_pagination(
        self, auth_client: AsyncClient, regular_user, admin_user, make_wallet, test_session
    ):
        mine = await make_wallet(regular_user)
        other = await make_wallet(admin_user)
        self.make_tx(test_session, "0xold" + "0" * 10, mine.address, other.address, datetime(2026, 10, 1))
        self.make_tx(test_session, "0xnew" + "0" * 10, mine.address, other.address, datetime(2026, 10, 2))
        await test_session.commit()

        first = await auth_client.get("/wallets/transactions", params={"limit": 1})
        second = await auth_client.get("/wallets/transactions", params={"limit": 1, "offset": 1})
        assert [t["tx_hash"] for t in first.json()] == ["0xnew" + "0" * 10]
        assert [t["tx_hash"] for t in second.json()] == ["0xold" + "0" * 10]

    async def test_transactions_pagination_bounds(self, auth_client: AsyncClient):
        assert (await auth_client.get("/wallets/transactions", params={"limit": 201})).status_code == 422
        assert (await auth_client.get("/wallets/transactions", params={"offset": -1})).status_code == 422


class TestMyWallet:

    async def test_my_wallet_requires_auth(self, client: AsyncClient):
        response = await client.get("/wallets/my_wallet")
        assert response.status_code == 401

    async def test_my_wallet_not_found(self, auth_client: AsyncClient):
        response = await auth_client.get("/wallets/my_wallet")
        assert response.status_code == 404

    async def test_my_wallet_does_not_expose_private_key(
        self, auth_client: AsyncClient, regular_user, make_wallet
    ):
        wallet = await make_wallet(regular_user)

        response = await auth_client.get("/wallets/my_wallet")
        assert response.status_code == 200
        data = response.json()
        assert data["address"] == wallet.address
        assert data["id"] == wallet.id
        assert "created_at" in data
        assert "encrypted_private_key" not in data
        assert "private_key" not in json.dumps(data)


class TestBalances:

    async def test_balances_require_auth(self, client: AsyncClient):
        response = await client.get("/wallets/ballances")
        assert response.status_code == 401

    async def test_balances_empty(self, auth_client: AsyncClient):
        response = await auth_client.get("/wallets/ballances")
        assert response.status_code == 200
        assert response.json() == {}

    async def test_balances_with_data(
        self, auth_client: AsyncClient, regular_user, make_balance
    ):
        await make_balance(regular_user, "BTC", "1.5")

        response = await auth_client.get("/wallets/ballances")
        assert response.status_code == 200
        data = response.json()
        assert float(data["BTC"]) == 1.5

    async def test_balances_multiple_currencies_only_own(
        self, auth_client: AsyncClient, regular_user, admin_user, make_balance
    ):
        await make_balance(regular_user, "bitcoin", "2")
        await make_balance(regular_user, "ethereum", "10")
        await make_balance(admin_user, "bitcoin", "999")

        response = await auth_client.get("/wallets/ballances")
        data = response.json()
        assert set(data) == {"bitcoin", "ethereum"}
        assert float(data["bitcoin"]) == 2
        assert float(data["ethereum"]) == 10
