from pydantic import BaseModel,Field, EmailStr,ConfigDict
from datetime import datetime
from decimal import Decimal

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class UserRegister(UserLogin):
    name: str

class UserResponse(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr

class UserUpdate(BaseModel):
    name:str|None = None
    email: EmailStr|None = None

class PasswordChange(BaseModel):
    old_password: str = Field(min_length=8,max_length=128)
    new_password: str = Field(min_length=8,max_length=128)

class AdminUserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    role: str | None = None


class AdminUserResponse(UserResponse):
    role: str
    is_blocked: bool
    blocked_at: datetime | None = None
    block_reason: str | None = None


class CancelRequest(BaseModel):
    tx_hash: str = Field(min_length=10,max_length=255,description="Transaction hash for cancellation")

class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    address: str
    created_at: datetime

class BalanceResponse(BaseModel):
    currency:str = Field(min_length=2,max_length=20,description="Enter symbol(BTC,ETH,..) or coingecko id(bitcoin,ethereum,..)")
    amount: Decimal = Field(gt=0)

class SendRequest(BaseModel):
    currency: str = Field(min_length=2,max_length=20,description="Enter symbol(BTC,ETH,..) or coingecko id(bitcoin,ethereum,..)")
    amount: Decimal = Field(gt=0)
    to_address: str = Field(min_length=10, max_length=255)


class SendResponse(BaseModel):
    tx_hash: str
    status: str
    currency: str
    amount: str
    gas_fee: str
    to_address: str
    from_address: str
    internal: bool

class OperationStatusResponse(BaseModel):
    operation_id: str
    status: str
    type: str
    result: dict | None = None

class SpecificCoin(BaseModel):
    coin_id: str = Field(max_length=50,description="Coin name('bitcoin', 'ethereum',...)")
    hours: int = Field(gt=0,le=720,default=24)

class CoinOrdinaryResponse(BaseModel):
    currency: str
    current_price: Decimal|float
    price_change_percentage: Decimal
    last_updated: datetime

class CoinDataResponse(BaseModel):
    currency: str
    amount: str
    total_purchase_value_usd: str
    total_current_value_usd: str
    current_value_usd: str
    profit_usd: str
    percentage_change: str
    last_updated_at: datetime

