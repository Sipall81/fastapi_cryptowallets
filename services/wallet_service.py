import hashlib
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import HTTPException
import httpx

from eth_account import Account
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import WalletModel, BalanceModel, BlockchainTransactionModel
from config import settings
from models import WalletResponse,BalanceResponse
from constants import feesservice

fernet = Fernet(settings.encryption_key.encode())

COINGECKO_IDS = feesservice.COINGECKO_IDS
class WalletService:
    GAS_FEES = feesservice.GAS_FEES
    async def create_wallet(self,user_id:int,session:AsyncSession) -> WalletResponse:
        account = Account.create()
        encrypted_key = fernet.encrypt(account.key.hex().encode()).decode()
        wallet = WalletModel(
            user_id=user_id,
            address=account.address,
            encrypted_private_key=encrypted_key
        )
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return WalletResponse(id=wallet.id,address=wallet.address,created_at=wallet.created_at)

    async def deposit(
            self,
            user_id: int,
            currency: str,
            amount: Decimal,
            session: AsyncSession,
            client: httpx.AsyncClient
    ):
        if currency.upper() in COINGECKO_IDS:
            currency=COINGECKO_IDS[currency.upper()]
        try:
            response = await client.get("/simple/price",params ={"vs_currencies": "usd", "ids": currency})
            response.raise_for_status()
        except:
            raise HTTPException(404,f"CoinGecko has no coin_id {currency}")

        price_usd = response.json().get(currency,{}).get("usd")
        result = await session.execute(select(BalanceModel).where(
            BalanceModel.user_id==user_id,
            BalanceModel.currency==currency
            )
        )
        balance = result.scalar_one_or_none()
        if not balance:
            balance = BalanceModel(
                user_id=user_id,
                currency=currency,
                amount=Decimal(0),
                avg_purchase_price_usd=price_usd if price_usd else Decimal(0),
                first_purchase_date=datetime.now(timezone.utc).replace(tzinfo=None) if price_usd else None,
                last_purchase_date=datetime.now(timezone.utc).replace(tzinfo=None) if price_usd else None
            )
            session.add(balance)
        else:
            if price_usd is not None and amount>0:
                old_total = balance.avg_purchase_price_usd*balance.amount
                new_total = price_usd*amount
                new_amount = balance.amount+amount
                balance.avg_purchase_price_usd = (
                        (old_total+new_total)/new_amount if new_amount>0 else Decimal(0)
                )
                balance.last_purchase_date = datetime.now(timezone.utc)
                if balance.first_purchase_date is None:
                    balance.first_purchase_date = datetime.now(timezone.utc)

        balance.amount+=amount
        await session.commit()
        await session.refresh(balance)
        return BalanceResponse(currency=currency,amount=amount)

    async def send_transaction(
            self,
            user_id: int,
            currency: str,
            to_address: str,
            amount: Decimal,
            session: AsyncSession
    ) -> dict:
        result = await session.execute(
            select(BalanceModel).where(
                BalanceModel.user_id==user_id,
                BalanceModel.currency==currency
            )
        )
        from_balance = result.scalar_one_or_none()
        if not from_balance:
            raise ValueError(f"Balance for {currency} not found")

        result = await session.execute(
            select(WalletModel).where(WalletModel.user_id == user_id)
        )
        from_wallet = result.scalar_one_or_none()
        if not from_wallet:
            raise ValueError("Wallet not found")

        gas_fee = self.GAS_FEES.get(currency, Decimal("0.001"))
        total = amount + gas_fee

        if from_balance.amount < total:
            raise ValueError(
                f"Insufficient balance. Need {total}, have {from_balance.amount}"
            )
        from_balance.amount -= total

        tx_hash = "0x" + hashlib.sha256(
            f"{user_id}{currency}{to_address}{amount}{datetime.now(timezone.utc)}".encode()
        ).hexdigest()

        tx = BlockchainTransactionModel(
            tx_hash=tx_hash,
            from_address=from_wallet.address,
            to_address=to_address,
            currency=currency,
            amount=amount,
            gas_fee=gas_fee,
            status="pending",
        )
        session.add(tx)

        result = await session.execute(
            select(WalletModel).where(WalletModel.address == to_address)
        )
        to_wallet = result.scalar_one_or_none()

        if to_wallet:
            result = await session.execute(
                select(BalanceModel).where(
                    BalanceModel.user_id == to_wallet.user_id,
                    BalanceModel.currency == currency,
                )
            )
            to_balance = result.scalar_one_or_none()
            if not to_balance:
                to_balance = BalanceModel(
                    user_id=to_wallet.user_id,
                    currency=currency,
                    amount=Decimal(0),
                )
                session.add(to_balance)
            to_balance.amount += amount
            print(f"Send {amount} {currency} to user with id {to_wallet.user_id}")
        else:
                print(f"Send to outer address {to_address}")
        await session.commit()
        await session.refresh(tx)

        return {
            "tx_hash": tx_hash,
            "status": "pending",
            "currency": currency,
            "amount": str(amount),
            "gas_fee": str(gas_fee),
            "to_address": to_address,
            "from_address": from_wallet.address,
            "internal": to_wallet is not None,  # ← внутренний перевод?
        }

    async def cancel_transaction(
        self,
        tx_hash: str,
        user_id: int,
        session: AsyncSession
    ) ->dict:
        result = await session.execute(
            select(BlockchainTransactionModel).where(
                BlockchainTransactionModel.tx_hash == tx_hash,
                BlockchainTransactionModel.status == "pending",
            )
        )
        tx = result.scalar_one_or_none()
        if not tx:
            raise ValueError("Transaction not found or not pending")

        result = await session.execute(
            select(WalletModel).where(WalletModel.user_id == user_id)
        )
        wallet = result.scalar_one_or_none()
        if not wallet or wallet.address != tx.from_address:
            raise ValueError("You are not the sender")

        result = await session.execute(
            select(BalanceModel).where(
                BalanceModel.user_id == user_id,
                BalanceModel.currency == tx.currency,
            )
        )
        balance = result.scalar_one_or_none()
        if balance:
            balance.amount += tx.amount + tx.gas_fee

        tx.status = "cancelled"
        tx.cancelled_at = datetime.now(timezone.utc)
        tx.note = "Cancelled by user"

        await session.commit()
        return {
            "info":"Transaction cancelled",
            "tx_hash": tx_hash,
            "refunded": str(tx.amount+tx.gas_fee)
        }

    async def withdraw(
            self,
            user_id:int,
            currency:str,
            to_address:str,
            amount:Decimal,
            session:AsyncSession
    )->dict:
        result = await session.execute(
            select(BalanceModel).where(
                BalanceModel.user_id==user_id,
                BalanceModel.currency==currency
            )
        )
        from_balance = result.scalar_one_or_none()
        if not from_balance:
            raise ValueError(f"balance for {currency} not found")

        result = await session.execute(
            select(WalletModel).where(WalletModel.user_id == user_id)
        )
        from_wallet = result.scalar_one_or_none()
        if not from_wallet:
            raise ValueError("Wallet not found")

        gas_fee = self.GAS_FEES.get(currency.upper(), Decimal("0.001"))
        total = amount + gas_fee
        if from_balance.amount < total:
            raise ValueError(
                f"Insufficient balance. Need {total}, have {from_balance.amount}"
            )
        from_balance.amount -= total

        tx_hash = "0x" + hashlib.sha256(
            f"{user_id}{currency}{to_address}{amount}{datetime.now(timezone.utc)}".encode()
        ).hexdigest()
        tx = BlockchainTransactionModel(
            tx_hash=tx_hash,
            from_address=from_wallet.address,
            to_address=to_address,
            currency=currency,
            amount=amount,
            gas_fee=gas_fee,
            status="pending",
            is_internal=False,
        )
        session.add(tx)

        await session.commit()
        await session.refresh(tx)

        return {
            "tx_hash": tx_hash,
            "status": "pending",
            "currency": currency,
            "amount": str(amount),
            "gas_fee": str(gas_fee),
            "to_address": to_address,
            "from_address": from_wallet.address,
            "is_withdraw": True,
        }

    async def get_wallet(self,user_id:int,session:AsyncSession) -> WalletModel:
        result = await session.execute(select(WalletModel).where(WalletModel.user_id==user_id))
        wallet = result.scalar_one_or_none()
        return wallet

    async def get_balance(self,user_id:int,session:AsyncSession) -> dict[str,Decimal]:
        result = await session.execute(select(BalanceModel).where(BalanceModel.user_id == user_id))
        balances = result.scalars().all()
        return {b.currency:b.amount for b in balances}



wallet_service = WalletService()

