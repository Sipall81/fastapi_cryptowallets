import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import aio_pika
import pytest

import rabbitmq_client
# Импорт на уровне модуля: к моменту выполнения тестов autouse-фикстура mock_rabbitmq
# уже подменяет rabbitmq_client.publish_message, а нам нужна настоящая функция.
from rabbitmq_client import close_rabbitmq, connect_to_rabbitmq
from rabbitmq_client import publish_message as real_publish_message


@pytest.fixture
def fake_channel(monkeypatch):
    channel = MagicMock()
    channel.declare_queue = AsyncMock()
    channel.default_exchange.publish = AsyncMock()
    monkeypatch.setattr(rabbitmq_client, "channel", channel)
    return channel


class TestPublishMessage:

    async def test_raises_when_not_connected(self, monkeypatch):
        monkeypatch.setattr(rabbitmq_client, "channel", None)
        with pytest.raises(RuntimeError, match="not connected"):
            await real_publish_message("wallet.send", {"a": 1})

    async def test_declares_durable_queue(self, fake_channel):
        await real_publish_message("wallet.send", {"a": 1})
        fake_channel.declare_queue.assert_awaited_once_with("wallet.send", durable=True)

    async def test_publishes_persistent_json_message(self, fake_channel):
        await real_publish_message("wallet.send", {"operation_id": "abc", "user_id": 1})

        publish = fake_channel.default_exchange.publish
        publish.assert_awaited_once()
        message = publish.await_args.args[0]
        assert json.loads(message.body) == {"operation_id": "abc", "user_id": 1}
        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
        assert publish.await_args.kwargs["routing_key"] == "wallet.send"

    async def test_non_json_types_are_stringified(self, fake_channel):
        await real_publish_message("wallet.send", {"amount": Decimal("0.5")})

        message = fake_channel.default_exchange.publish.await_args.args[0]
        assert json.loads(message.body) == {"amount": "0.5"}


class TestConnection:

    async def test_connect_stores_connection_and_channel(self, monkeypatch):
        monkeypatch.setattr(rabbitmq_client, "connection", None)
        monkeypatch.setattr(rabbitmq_client, "channel", None)
        channel = MagicMock()
        connection = MagicMock()
        connection.channel = AsyncMock(return_value=channel)
        connect_robust = AsyncMock(return_value=connection)
        monkeypatch.setattr(aio_pika, "connect_robust", connect_robust)

        await connect_to_rabbitmq()

        connect_robust.assert_awaited_once()
        assert rabbitmq_client.connection is connection
        assert rabbitmq_client.channel is channel

    async def test_close_closes_connection(self, monkeypatch):
        connection = MagicMock()
        connection.close = AsyncMock()
        monkeypatch.setattr(rabbitmq_client, "connection", connection)

        await close_rabbitmq()

        connection.close.assert_awaited_once()

    async def test_close_without_connection_is_noop(self, monkeypatch):
        monkeypatch.setattr(rabbitmq_client, "connection", None)
        await close_rabbitmq()
