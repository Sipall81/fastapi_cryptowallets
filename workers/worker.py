import asyncio
import aio_pika
import json
import os
import httpx
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import new_session,OperationModel
from services.wallet_service import wallet_service


RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

http_client: httpx.AsyncClient|None = None

async def process_wallet_create(message: aio_pika.IncomingMessage):
        async with message.process():
            data = json.loads(message.body.decode())
            operation_id = data["operation_id"]
            user_id = data["user_id"]
            try:
                async with new_session() as session:
                    wallet = await wallet_service.create_wallet(user_id,session)

                    await update_operation(
                        session,
                        operation_id,
                        status="completed",
                        result={"wallet_id":wallet.id,"address":wallet.address}
                    )
                print(f"Wallet {wallet.address} successfully created")
            except Exception as e:
                await update_operation_failed(operation_id,str(e))
                raise


async def process_deposit(message: aio_pika.IncomingMessage):
    async with message.process():
        data = json.loads(message.body.decode())
        operation_id = data["operation_id"]
        user_id = data["user_id"]
        currency = data["currency"]
        amount = Decimal(data["amount"])

        try:
            async with new_session() as session:
                balance = await wallet_service.deposit(
                    user_id=user_id,
                    currency=currency,
                    amount=amount,
                    session=session,
                    client=http_client
                )

                await update_operation(
                    session,
                    operation_id,
                    status="completed",
                    result={"currency": balance.currency, "amount": str(balance.amount)},
                )

            print(f"balance successfully updated: {balance.amount} {balance.currency}")

        except Exception as e:
            await update_operation_failed(operation_id, str(e))
            raise


async def process_send(message: aio_pika.IncomingMessage):
    async with message.process():
        data = json.loads(message.body.decode())
        operation_id = data["operation_id"]
        user_id = data["user_id"]
        currency = data["currency"]
        to_address = data["to_address"]
        amount = Decimal(data["amount"])

        try:
            async with new_session() as session:
                result_data = await wallet_service.send_transaction(
                    user_id=user_id,
                    currency=currency,
                    to_address=to_address,
                    amount=amount,
                    session=session,
                )

                await update_operation(
                    session,
                    operation_id,
                    status="completed",
                    result=result_data,
                )

            print(f"transaction {result_data['tx_hash']} completed")

        except Exception as e:
            await update_operation_failed(operation_id, str(e))
            raise

async def process_cancel(message:aio_pika.IncomingMessage):
    async with message.process():
        data = json.loads(message.body.decode())
        operation_id = data["operation_id"]
        user_id = data["user_id"]
        tx_hash = data["tx_hash"]

        try:
            async with new_session() as session:
                result_data = await wallet_service.cancel_transaction(
                    tx_hash=tx_hash,
                    user_id=user_id,
                    session=session
                )
                await update_operation(
                    session,
                    operation_id,
                    status="completed",
                    result = result_data
                )
            print(f"Transaction {tx_hash} succesfully cancelled")
        except Exception as e:
            await update_operation_failed(operation_id,str(e))
            raise

async def process_withdraw(message:aio_pika.IncomingMessage):
    async with message.process():
        data = json.loads(message.body.decode())
        operation_id = data["operation_id"]
        user_id = data["user_id"]
        currency = data["currency"]
        to_address = data["to_address"]
        amount = Decimal(data["amount"])

        try:
            async with new_session() as session:
                result_data = await wallet_service.withdraw(
                    user_id=user_id,
                    currency=currency,
                    to_address=to_address,
                    amount=amount,
                    session=session,
                )

                await update_operation(
                    session,
                    operation_id,
                    status="completed",
                    result=result_data,
                )

            print(f"withdraw {result_data['tx_hash']} completed")

        except Exception as e:
            await update_operation_failed(operation_id, str(e))
            raise


async def update_operation(
        session: AsyncSession,
        operation_id: str,
        status: str,
        result: dict
):
    result_obj = await session.execute(
        select(OperationModel).where(OperationModel.operation_id==operation_id)
    )
    operation = result_obj.scalar_one_or_none()
    if operation:
        operation.status = status
        operation.result = json.dumps(result)
        await session.commit()

async def update_operation_failed(operation_id: str, error: str):
    async with new_session() as session:
        await update_operation(
            session,
            operation_id,
            status="failed",
            result={"error": error},
        )



async def main():
    global http_client
    http_client = httpx.AsyncClient(
        base_url=settings.coingecko_url,
        timeout=10.0
    )

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()

    await channel.set_qos(prefetch_count=10)

    queue_create = await channel.declare_queue("wallet.create",durable=True)
    queue_deposit = await channel.declare_queue("wallet.deposit",durable=True)
    queue_send = await channel.declare_queue("wallet.send",durable=True)
    queue_cancel = await channel.declare_queue("wallet.cancel",durable=True)
    queue_withdraw = await channel.declare_queue("wallet.withdraw",durable=True)

    await queue_create.consume(process_wallet_create)
    await queue_deposit.consume(process_deposit)
    await queue_send.consume(process_send)
    await queue_cancel.consume(process_cancel)
    await queue_withdraw.consume(process_withdraw)

    print("Worker is on. Waiting messages...")
    await asyncio.Future()



if __name__ == "__main__":
    asyncio.run(main())