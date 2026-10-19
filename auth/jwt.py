from datetime import datetime, timezone,timedelta
from jose import jwt,JWTError
from fastapi import HTTPException,status
from config import settings

def create_access_token(user_id: int,role:str = "user") ->str:
    expire = datetime.now(timezone.utc)+timedelta(minutes = 15)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire
    }
    return jwt.encode(payload,settings.secret_key,algorithm=settings.algorithm)

def create_refresh_token(user_id:int) -> str:
    expire = datetime.now(timezone.utc)+timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire
    }
    return jwt.encode(payload,settings.secret_key,algorithm=settings.algorithm)

def decode_token(token:str):
    try:
        payload = jwt.decode(token,settings.secret_key,algorithms=[settings.algorithm])
        return payload
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                              detail=f"Invalid token: {e}",
                            headers = {"WWW-Authenticate": "Bearer"})