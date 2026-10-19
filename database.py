from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import inspect,ForeignKey,String,func,Numeric,UniqueConstraint,Index,Text
from fastapi import FastAPI,Depends
from typing import Annotated
from contextlib import asynccontextmanager
from redis_client import redis_client
from rabbitmq_client import connect_to_rabbitmq, close_rabbitmq
from datetime import datetime
from decimal import Decimal
import httpx
from rate_limiter import rate_limiter
from config import settings

engine = create_async_engine(settings.database_url,pool_pre_ping=True)

new_session = async_sessionmaker(engine,expire_on_commit=False)

class Base(DeclarativeBase):
    def to_dict(self):
        return {
            c.key: getattr(self, c.key) for c in inspect(self).mapper.column_attrs
        }

class UserModel(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name:Mapped[str]
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="user")

    is_blocked: Mapped[bool] = mapped_column(default=False)
    blocked_at: Mapped[datetime | None]
    block_reason: Mapped[str | None]

class WalletModel(Base):
    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"),unique=True)
    address: Mapped[str] = mapped_column(String(255),index=True,unique=True)
    encrypted_private_key: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

class BalanceModel(Base):
    __tablename__ = "balances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    currency: Mapped[str] = mapped_column(String(20),index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(36,18),default=0)
    avg_purchase_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(36, 18), default=0
    )
    first_purchase_date: Mapped[datetime | None]
    last_purchase_date: Mapped[datetime | None]

    __table_args__ = (
        UniqueConstraint("user_id","currency",name="uq_user_currency"),
    )


class   BlockchainTransactionModel(Base):
    __tablename__ = "blockchain_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)

    tx_hash: Mapped[str] = mapped_column(String(255),unique=True,index=True,)
    from_address: Mapped[str] = mapped_column(String(255), index=True)
    to_address: Mapped[str] = mapped_column(String(255), index=True)

    currency: Mapped[str] = mapped_column(String(20), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(36, 18))
    gas_fee: Mapped[Decimal] = mapped_column(Numeric(36, 18), default=0)

    # pending   — отправлена, ждёт подтверждения
    # confirmed — подтверждена (в блоке)
    # failed    — ошибка
    # cancelled — отменена (возврат)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    block_number: Mapped[int | None]  # номер блока (при подтверждении)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    confirmed_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]

    is_internal: Mapped[bool] = mapped_column(default=False)  # внутренний перевод?
    note: Mapped[str | None]  # комментарий

    __table_args__ = (
        Index("ix_tx_from_currency", "from_address", "currency"),
        Index("ix_tx_to_currency", "to_address", "currency"),
        Index("ix_tx_status_created", "status", "created_at"),
    )

class OperationModel(Base):
    __tablename__="operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id:Mapped[str] = mapped_column(String(64),index=True,unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime|None]

async def get_session():
    async with new_session() as session:
        yield session

SessionDep = Annotated[AsyncSession,Depends(get_session)]

@asynccontextmanager
async def lifespan(app:FastAPI):
    redis_ping = await redis_client.ping()
    print(f"{redis_ping=}")

    app.state.rate_limiter = rate_limiter
    await connect_to_rabbitmq()

    app.state.http_client=httpx.AsyncClient(
        base_url=settings.coingecko_url,
        timeout=httpx.Timeout(10.0),
        headers={"Accept": "application/json"},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tables created")
    yield   
    await app.state.http_client.aclose()
    await redis_client.aclose()
    await close_rabbitmq()
    await engine.dispose()
