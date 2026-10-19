from fastapi import FastAPI
import uvicorn
from database import lifespan
from routers.users import router as users_router
from routers.coingecko import router as crypto_router
from routers.wallets import router as wallets_router
from routers.admin import router as admin_router
from middleware import RateLimiterMiddleware


app = FastAPI(lifespan=lifespan)
app.add_middleware(RateLimiterMiddleware)
app.include_router(users_router)
app.include_router(admin_router)
app.include_router(crypto_router)
app.include_router(wallets_router)


if __name__=="__main__":
    uvicorn.run("main:app",reload=True)





