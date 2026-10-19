from fastapi import APIRouter,HTTPException,status,Query,Depends
from database import SessionDep
from sqlalchemy import select
from auth.dependencies import CurrentAdmin
from database import UserModel
from datetime import datetime,timezone
from redis_client import redis_client
import json
from models import AdminUserUpdate,AdminUserResponse
from typing import Annotated

router = APIRouter(prefix="/admin",tags=["admin"])

@router.patch("/change_user_data/{user_id}")
async def change_user_data(
        user_id:int,
        data:Annotated[AdminUserUpdate,Depends()],
        admin:CurrentAdmin,
        session:SessionDep
):
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    update_data = data.model_dump(exclude_unset=True,exclude_none=True)
    if not update_data:
        return {"info":"No changes were made"}
    if update_data["role"] not in ("user","admin"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid role. Role might be 'user' or 'admin'"
        )
    if "email" in update_data:
        result = await session.execute(
            select(UserModel).where(
                UserModel.email==update_data["email"],
                UserModel.id!=user_id
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
    for key,value in update_data.items():
        setattr(user,key,value)
    await session.commit()
    await session.refresh(user)
    await redis_client.delete(f"from_admin:user:{user.id}",f"from_user:user:{user.id}","users:all")

    return {"info":"User's data updated","user_id":user.id}

@router.post("/give_admin")
async def give_user_admin(user_id:int,admin:CurrentAdmin,session:SessionDep):
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    if user.role=="admin":
        return {"info":f"user with id {user_id} is already admin"}

    user.role = "admin"
    await session.commit()
    await session.refresh(user)
    return {
        "info":"user's role updated",
        "user_id": user.id,
        "user_role": user.role
    }


@router.get("/user/{id}",response_model=AdminUserResponse)
async def user_info(id:int,user:CurrentAdmin,session:SessionDep):
    cached_key = f"from_admin:user:{id}"
    cached = await redis_client.get(cached_key)
    if cached:
        return json.loads(cached)

    result = await session.execute(select(UserModel).where(UserModel.id == id))
    found_user = result.scalar_one_or_none()
    if not found_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found"
        )
    user_response = AdminUserResponse.model_validate(found_user)
    await redis_client.setex(cached_key,60,json.dumps(user_response.model_dump(mode="json")))
    return user_response

@router.delete("/users/{user_id}/block",response_model=dict[str,str|int])
async def block_user(
        user_id:int,
        reason: str,
        admin:CurrentAdmin,
        session:SessionDep
):
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if user.is_blocked:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail = "User already blocked"
        )
    user.is_blocked = True
    user.blocked_at =datetime.now(timezone.utc).replace(tzinfo=None)
    user.block_reason = reason

    await session.commit()
    return {"info":"User soft-deleted","user_id":user.id}

@router.post("/users/{user_id}/unblock")
async def unblock_user(
        user_id:int,
        admin:CurrentAdmin,
        session:SessionDep
):
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if not user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not blocked"
        )
    user.is_blocked = False
    user.blocked_at = None
    user.block_reason = None
    await session.commit()
    return {"info":"User unblocked","user_id":user.id}

@router.get("/users")
async def list_users(
        admin:CurrentAdmin,
        session:SessionDep,
        blocked:bool|None =None,
        role:str|None = None,
        limit: int = Query(50,le=200),
        offset:int = Query(0,ge=0)
):
    query = select(UserModel)
    if blocked is not None:
        query = query.where(UserModel.is_blocked==blocked)
    if role:
        query = query.where(UserModel.role==role)
    result = await session.execute(query.limit(limit).offset(offset))
    return result.scalars().all()