import aio_pika
import os
import json
from aio_pika import Message, DeliveryMode
from config import settings

connection: aio_pika.RobustConnection | None = None
channel: aio_pika.RobustChannel | None = None


async def connect_to_rabbitmq():
    global connection, channel
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    print("RabbitMQ connected")


async def close_rabbitmq():
    global connection
    if connection:
        await connection.close()
        print("RabbitMQ disconnected")


async def publish_message(queue_name: str, message: dict):
    if not channel:
        raise RuntimeError("RabbitMQ is not connected")

    queue = await channel.declare_queue(queue_name, durable=True)

    await channel.default_exchange.publish(
        Message(
            body=json.dumps(message,default=str).encode(),
            delivery_mode=DeliveryMode.PERSISTENT,
        ),
        routing_key=queue_name,
    )
    print(f"Message '{message}' in  queue '{queue_name}'")

