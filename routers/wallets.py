import json
import uuid
from decimal import Decimal
from fastapi import APIRouter,HTTPException,status,Depends,Query
from sqlalchemy import select
from typing import Annotated

from database import SessionDep, OperationModel,WalletModel,BlockchainTransactionModel
from models import (
    WalletResponse,
    SendRequest,
    CancelRequest,
    OperationStatusResponse,
    BalanceResponse
)
from services.wallet_service import wallet_service
from rabbitmq_client import publish_message
from auth.dependencies import CurrentUser,CurrentUserBase,HttpClient
from constants import feesservice

COINGECKO_IDS = feesservice.COINGECKO_IDS

router = APIRouter(prefix="/wallets",tags=["wallets"])

@router.post("/create",response_model=OperationStatusResponse,status_code=status.HTTP_202_ACCEPTED)
async def wallet_create(user: CurrentUser,session:SessionDep):
    result = await session.execute(select(WalletModel).where(WalletModel.user_id==user.id))
    wallet = result.scalar_one_or_none()
    if wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a wallet"
        )
    operation_id = uuid.uuid4().hex
    operation = OperationModel(
        operation_id = operation_id,
        user_id = user.id,
        type ="wallet_create",
        payload = json.dumps({"user_id":user.id})
    )
    session.add(operation)
    await session.commit()

    await publish_message(
        queue_name = "wallet.create",
        message = {"operation_id":operation_id,"user_id":user.id}
    )
    return OperationStatusResponse(
        operation_id = operation_id,
        status = "pending",
        type = "wallet_create"
    )

@router.post("/deposit",response_model=OperationStatusResponse,status_code=status.HTTP_202_ACCEPTED)
async def deposit(
        balance:Annotated[BalanceResponse,Depends()],
        user:CurrentUser,
        session:SessionDep,
):
    operation_id = uuid.uuid4().hex
    operation = OperationModel(
        operation_id = operation_id,
        user_id = user.id,
        type = "deposit",
        payload = json.dumps({
            "user_id":user.id,
            "currency":balance.currency,
            "amount": str(balance.amount)
        })
    )
    session.add(operation)
    await session.commit()

    await publish_message(
        queue_name = "wallet.deposit",
        message={
            "operation_id": operation_id,
            "user_id": user.id,
            "currency":balance.currency.upper(),
            "amount": str(balance.amount),
        }
    )
    return OperationStatusResponse(
        operation_id=operation_id,
        status="pending",
        type="deposit"
    )

@router.post("/send",response_model=OperationStatusResponse,status_code=status.HTTP_202_ACCEPTED)
async def send(
        data: Annotated[SendRequest,Depends()],
        user:CurrentUser,
        session:SessionDep
):
    if data.currency.upper() in COINGECKO_IDS.keys():
        data.currency=COINGECKO_IDS[data.currency.upper()]

    operation_id = uuid.uuid4().hex
    operation = OperationModel(
        operation_id=operation_id,
        user_id=user.id,
        type="send",
        payload = json.dumps({
            "user_id":user.id,
            "currency":data.currency.upper(),
            "amount":str(data.amount),
            "to_address":data.to_address
        })
    )
    session.add(operation)
    await session.commit()

    await publish_message(
        queue_name="wallet.send",
        message = {
            "operation_id":operation_id,
            "user_id":user.id,
            "currency":data.currency,
            "amount":str(data.amount),
            "to_address":data.to_address
        }
    )
    return OperationStatusResponse(
        operation_id=operation_id,
        status = "pending",
        type="send"
    )

@router.post(
    "/cancel_transaction",
    response_model=OperationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def cancel_transaction(
        data:CancelRequest,
        user:CurrentUser,
        session:SessionDep
):
    operation_id = uuid.uuid4().hex
    operation = OperationModel(
        operation_id=operation_id,
        user_id=user.id,
        type="cancel",
        payload=json.dumps({
            "user_id":user.id,
            "tx_hash":data.tx_hash
        })
    )
    session.add(operation)
    await session.commit()

    await publish_message(
        queue_name="wallet.cancel",
        message={
            "operation_id":operation_id,
            "user_id":user.id,
            "tx_hash":data.tx_hash
                 }
    )

    return OperationStatusResponse(
        operation_id=operation_id,
        status="pending",
        type="cancel"
    )

@router.post(
    "/withdraw",
    response_model=OperationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED
)
async def withdraw(
        data:Annotated[SendRequest,Depends()],
        user:CurrentUserBase,
        session:SessionDep
):
    if data.currency.upper() in COINGECKO_IDS.keys():
            data.currency=COINGECKO_IDS[data.currency.upper()]
    result = await session.execute(
        select(WalletModel).where(WalletModel.address == data.to_address)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Cannot withdraw to internal address",
        )
    operation_id = uuid.uuid4().hex
    operation = OperationModel(
        operation_id=operation_id,
        user_id=user.id,
        type="withdraw_blocked",
        payload=json.dumps({
            "user_id": user.id,
            "currency": data.currency.upper(),
            "to_address": data.to_address,
            "amount": str(data.amount),
        }),
    )
    session.add(operation)
    await session.commit()

    await publish_message(
        queue_name="wallet.withdraw",
        message={
            "operation_id": operation_id,
            "user_id": user.id,
            "currency": data.currency,
            "to_address": data.to_address,
            "amount": str(data.amount),
        },
    )

    return OperationStatusResponse(
        operation_id=operation_id,
        status="pending",
        type="withdraw_blocked",
    )

@router.get("/operations/{operation_id}",response_model=OperationStatusResponse)
async def get_operation(
        operation_id:str,
        user:CurrentUser,
        session:SessionDep
):
    result = await session.execute(
        select(OperationModel).where(
            OperationModel.operation_id==operation_id,
            OperationModel.user_id==user.id
        )
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail = "Operation not found"
        )
    return OperationStatusResponse(
        operation_id = operation.operation_id,
        status = operation.status,
        type = operation.type,
        result = json.loads(operation.result) if operation.result else None
    )

@router.get("/transactions")
async def my_transactions(
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    wallet = await wallet_service.get_wallet(user.id, session)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found"
        )
    result = await session.execute(
        select(BlockchainTransactionModel)
        .where(
            (BlockchainTransactionModel.from_address == wallet.address) |
            (BlockchainTransactionModel.to_address == wallet.address)
        )
        .order_by(BlockchainTransactionModel.created_at.desc())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()

@router.get("/operations")
async def my_operations(user:CurrentUser,session:SessionDep,limit:int=50):
    result = await session.execute(
        select(OperationModel)
        .where(OperationModel.user_id==user.id)
        .order_by(OperationModel.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()

@router.get("/my_wallet",response_model=WalletResponse)
async def get_my_wallet(user:CurrentUser,session:SessionDep):
    wallet = await wallet_service.get_wallet(user.id,session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet

@router.get("/ballances",response_model=dict[str,str])
async def get_my_balances(user:CurrentUser,session:SessionDep):
    balances = await wallet_service.get_balance(user.id,session)
    return {currency:str(amount) for currency,amount in balances.items()}
