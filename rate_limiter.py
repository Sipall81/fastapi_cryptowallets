import random
import time
from typing import Annotated
from fastapi import HTTPException, Depends, Request, status
import redis.asyncio as redis
from redis_client import redis_client

class RateLimiter:
    def __init__(self,redis:redis.Redis):
        self._redis = redis

    async def is_limited(self,
                         ip_address:str,
                         endpoint:str,
                         max_requests:int,
                         window_seconds:int)->bool:
        key = f"rl:{endpoint}:{ip_address}"
        current_ms = int(time.time()*1000)
        current_request = f"{current_ms}-{random.randint(0,100_000)}"
        window_start_ms = current_ms-window_seconds*1000
        async with self._redis.pipeline() as pipe:
            await pipe.zremrangebyscore(key,min=0,max=window_start_ms)
            await pipe.zadd(key, mapping={current_request: current_ms})
            await pipe.zcard(key)
            await pipe.expire(key,window_seconds)
            res = await pipe.execute()
        _,_,count,_ = res
        if count>max_requests:
            return True
        return False

rate_limiter = RateLimiter(redis_client)


async def get_rate_limiter()->RateLimiter:
    return rate_limiter



def rate_limiter_factory(
    endpoint: str,
    max_requests: int,
    window_seconds: int,
):
    async def dependency(
        request: Request,
        rate_limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    ):
        ip_address = request.client.host if request.client else "unknown"

        if await rate_limiter.is_limited(
            ip_address,
            endpoint,
            max_requests,
            window_seconds,
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try make requests later"

            )
    return dependency

