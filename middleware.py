from fastapi import Request
from typing import Callable
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimiterMiddleware(BaseHTTPMiddleware):

    EXCLUDED = {"/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}
    SPECIAL_LIMITS = {
        "users/login": (5, 60),
        "users/register": (3, 300),
        "users/refresh_tokens": (10, 60),
        "users/all":(10,180),
        "users/me/change_password":(10,120),
        "admin/users":(15,120),
        "wallets/create":(5,180),
    }
    DEFAULT_LIMIT= (20,60)
    async def dispatch(self,request:Request,call_next:Callable):
        if request.url.path in self.EXCLUDED:
            return await call_next(request)
        rate_limiter = request.app.state.rate_limiter
        ip = request.client.host if request.client else "unknown"
        path = request.url.path
        max_request, window =  self.DEFAULT_LIMIT if path not in self.SPECIAL_LIMITS else self.SPECIAL_LIMITS[path]
        if await rate_limiter.is_limited(ip,path,max_request,window):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try make requests later"},
                headers={"Retry-After": str(window)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"]=str(max_request)
        return response

