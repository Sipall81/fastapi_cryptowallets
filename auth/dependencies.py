from typing import Annotated
from fastapi import Depends,HTTPException,status,Cookie,Request
from sqlalchemy import select

from database import SessionDep,UserModel
from auth.jwt import decode_token
import httpx
from rate_limiter import RateLimiter

async def get_current_user_base(
        session:SessionDep,
        access_token: str|None = Cookie(None)
)->UserModel:
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate":"Bearer"}
        )
    payload = decode_token(access_token)
    if payload.get("type")!="access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type"
        )
    user_id = int(payload.get("sub"))
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return user

async def get_current_user(
        user:Annotated[UserModel,Depends(get_current_user_base)]
)->UserModel:
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is blocked. Only withdrawal allowed\n"
                   f"block reason: {user.block_reason}"
        )
    return user

async def get_blocked_user(
        user:Annotated[UserModel,Depends(get_current_user_base)]
)->UserModel:
    if not user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is not blocked. Use /wallets/send instead.",
        )
    return user


async def get_current_admin(
        user:Annotated[UserModel,Depends(get_current_user)]
)->UserModel:
    if user.role!="admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail = "Admin access required"
        )
    return user

async def get_http_client(request:Request)->httpx.AsyncClient:
    return request.app.state.http_client

async def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


CurrentUserBase=Annotated[UserModel,Depends(get_current_user_base)]
CurrentUser = Annotated[UserModel,Depends(get_current_user)]
BlockedUser = Annotated[UserModel,Depends(get_blocked_user)]
CurrentAdmin = Annotated[UserModel,Depends(get_current_admin)]

HttpClient = Annotated[httpx.AsyncClient,Depends(get_http_client)]



