from fastapi import APIRouter,HTTPException, Depends, status,Response,Cookie
from database import SessionDep
from sqlalchemy import select
from models import UserResponse, UserRegister,UserLogin,UserUpdate,PasswordChange
from database import UserModel
from typing import Annotated,List
import json
from redis_client import redis_client
from auth.password import hash_password,verify_password
from auth.jwt import create_access_token,create_refresh_token, decode_token
from auth.dependencies import CurrentUser
from config import settings

router = APIRouter(prefix="/users",tags=["users"])

@router.get("/")
async def greeting()->str:
    return "hi pall"

@router.get("/user/{id}",response_model=UserResponse)
async def user_info(id:int,user:CurrentUser,session:SessionDep):
    cached_key = f"from_user:user:{id}"
    cached = await redis_client.get(cached_key)
    if cached:
        return json.loads(cached)

    result = await session.execute(select(UserModel).where(UserModel.id==id))
    found_user = result.scalar_one_or_none()
    if not found_user:
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail=f"User not found"
        )
    await redis_client.setex(f"from_user:user:{id}",300,json.dumps(UserResponse.model_validate(found_user).model_dump(mode="json")))
    return UserResponse.model_validate(found_user)

@router.get("/all_users",response_model=List[UserResponse])
async def all_users(user:CurrentUser,session:SessionDep):
    cached = await redis_client.get("users:all")
    if cached:
        return json.loads(cached)

    result = await session.execute(select(UserModel))
    users = result.scalars().all()
    if not users:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No users for now"
        )
    users_data=[UserResponse.model_validate(user).model_dump(mode="json") for user in users]
    await redis_client.setex("users:all",300,json.dumps(users_data))
    return users


@router.post("/register",response_model=UserResponse)
async def register_user(new_user:Annotated[UserRegister,Depends()],session:SessionDep):
    result = await session.execute(select(UserModel).where(UserModel.email==new_user.email))
    user = result.scalar_one_or_none()
    if user:
        raise HTTPException(status_code=400, detail=f"User with email {new_user.email} already exists")
    result = await session.execute(select(UserModel))
    is_first = result.scalars().first() is None
    user = UserModel(name=new_user.name,
                     email=new_user.email,
                     hashed_password=hash_password(new_user.password),
                     role="admin" if is_first else "user")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserResponse.model_validate(user)

@router.post("/login")
async def login_user(user_data:Annotated[UserLogin,Depends()],session:SessionDep,response:Response):
    result = await session.execute(select(UserModel).where(UserModel.email==user_data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(user_data.password,user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail= "Invalid email or password")

    access_token = create_access_token(user.id,user.role)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_expire_minutes * 60
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.refresh_token_expire_days * 3600 *24
    )

    return {"info":f"Welcome,{user.name}"}

@router.post("/refresh")
async def refresh_tokens(response:Response,user:CurrentUser, session:SessionDep, refresh_token: str|None = Cookie(None)):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="No refresh token")

    payload = decode_token(refresh_token)
    user_id = int(payload.get("sub"))
    user_role = payload.get("role")
    result = await session.execute(select(UserModel).where(UserModel.id==user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    access_token = create_access_token(user.id,user_role)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age = settings.access_token_expire_minutes*60
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.refresh_token_expire_days * 3600 * 24
    )

    return {"info":"tokens refreshed"}

@router.patch("/update_data")
async def update_user_data(
        data:UserUpdate,
        user:CurrentUser,
        session:SessionDep
):
    update_data = data.model_dump(exclude_unset=True,exclude_none=True)
    if "email" in update_data:
        result = await session.execute(
            select(UserModel).where(
                UserModel.email==update_data["email"],
                UserModel.id!=user.id
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
    return {"info":"new data applies","user_id":user.id,"name":user.name,"email":user.email}

@router.post("/logout")
async def logout(response:Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"info":"Logged out"}

@router.get("/me")
async def my_data(user:CurrentUser):
    return user

@router.post("/me/change_password")
async def change_password(
        data: PasswordChange,
        user:CurrentUser,
        session:SessionDep
):
    if not verify_password(data.old_password,user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect"
        )
    user.hashed_password=hash_password(data.new_password)
    await session.commit()
    return {"info":"Password changed"}
