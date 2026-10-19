from auth.dependencies import HttpClient,CurrentUser
from fastapi import APIRouter,HTTPException,Path,Query,Response,Depends
from sqlalchemy import select
import httpx
import io
from datetime import datetime,timedelta,timezone
from models import SpecificCoin,CoinDataResponse,CoinOrdinaryResponse
from typing import Annotated
import math
from redis_client import redis_client
import json
import base64
from database import SessionDep,BalanceModel
from decimal import Decimal
from constants import feesservice

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

router = APIRouter(prefix="/crypto",tags=["crypto"])

COINGECKO_IDS = feesservice.COINGECKO_IDS

def _calculate_coin_data(
        balance:BalanceModel,
        current_price:Decimal|None,
        last_updated_at:datetime|None = None
)->CoinDataResponse:
    amount = balance.amount
    avg_price = balance.avg_purchase_price_usd
    print(f"{type(amount)=} {amount=} {type(current_price)=} {current_price=}")
    total_purchase = amount*avg_price
    total_current =  amount*(current_price or Decimal("0"))
    profit = total_current-total_purchase
    
    percentage = (
        (profit / total_purchase * 100)
        if total_purchase > 0
        else Decimal("0")
    )
    if last_updated_at is not None:
        last_update = last_updated_at
    else:
        last_update = datetime.now(timezone.utc)
    return CoinDataResponse(
        currency=balance.currency,
        amount=str(amount),
        total_purchase_value_usd=str(total_purchase),
        total_current_value_usd=str(total_current),
        current_value_usd=str(current_price),
        profit_usd=str(profit),
        percentage_change=str(percentage.quantize(Decimal("0.01"))),
        last_updated_at = last_update
    )


@router.get("/top_coins",summary="Top cryptocurrency",response_model=list[CoinOrdinaryResponse])
async def get_top_coins(
        client: HttpClient,
        per_page: int = Query(20,ge=1,le=50,description="coins amount(1,50)"),
        page: int = Query(1,ge=1,description="page number"),
        price_change_percentage: str = Query(
            "24h",
            description="Valid values: 1h, 24h, 7d, 14d, 30d, 200d, 1y",
    )
):
    cache_key = f"cg:top:{per_page}:{page}:{price_change_percentage}"
    cached = await redis_client.get(cache_key)
    if cached:
        return [CoinOrdinaryResponse.model_validate(item) for item in json.loads(cached)]
    date = price_change_percentage.strip().lower()
    params = {
        "vs_currency":"usd",
        "order":"market_cap_desc",
        "per_page":per_page,
        "page": page,
        "price_change_percentage":date
    }
    try:
        response = await client.get("/coins/markets",params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"CoinGecko API error: {e.response.text}",
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="CoinGecko service temporarily unavailable",
        )
    coins_data = [
        CoinOrdinaryResponse(
            currency=coin["id"],
            current_price=coin["current_price"],
            price_change_percentage=coin[f"price_change_percentage_{date}_in_currency"],
            last_updated=datetime.fromisoformat(coin["last_updated"].replace("Z", "+00:00"))
        ).model_dump(mode="json") for coin in data
    ]
    await redis_client.setex(cache_key, 60, json.dumps(coins_data))
    return coins_data




@router.get("/coin/chart")
async def get_coin_chart(
        client:HttpClient,
        coin:Annotated[SpecificCoin,Depends()]
):
    if coin.coin_id.upper() in COINGECKO_IDS.keys():
        coin.coin_id=COINGECKO_IDS[coin.coin_id.upper()]
    cache_key = f"cg:chart:{coin.coin_id}:{coin.hours}"
    cached = await redis_client.get(cache_key)
    if cached:
        png_bytes = base64.b64decode(cached)
        return Response(content=png_bytes,media_type="image/png")


    params={"vs_currency":"usd","days":math.ceil(coin.hours/24)}
    try:
        response = await client.get(
            f"/coins/{coin.coin_id}/market_chart",
            params=params
        )
        response.raise_for_status()
        data = response.json()
        prices = data["prices"]
        now_ms = datetime.now().timestamp()*1000
        cutoff_ms = now_ms-(coin.hours*3600*1000)
        points = [p for p in prices if p[0]>=cutoff_ms]
        if not points:
            raise HTTPException(404,"No data for this period")
        timestamps = [datetime.fromtimestamp(p[0]/1000) for p in points]
        prices = [p[1] for p in points]
        fig,ax = plt.subplots(figsize=(10,4))
        ax.plot(timestamps,prices,linewidth=2,color="#006400")
        ax.fill_between(timestamps, prices, alpha=0.1, color='#006400')
        ax.set_title(f"{coin.coin_id.capitalize()}/ USD — last {timedelta(hours=coin.hours)}")
        ax.set_xlabel("Time")
        ax.set_ylabel("Price (USD)")
        ax.grid(True,alpha=0.3)
        fig.autofmt_xdate()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        plt.close(fig)
        buf.seek(0)
        png_bytes = buf.getvalue()

        await redis_client.setex(cache_key,150,base64.b64encode(png_bytes).decode())
        return Response(
            content=png_bytes,
            media_type="image/png"
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, f"Coin '{coin.coin_id}' not found")
        raise HTTPException(e.response.status_code, f"CoinGecko error: {e.response.text}")
    except httpx.RequestError:
        raise HTTPException(503, "CoinGecko service temporarily unavailable")


@router.get("/coin/{coin_id}",summary="specific coin data")
async def get_coin(
        client:HttpClient,
        coin_id: str = Path(...,description="coin name('bitcoin', 'ethereum',...)")
):
    if coin_id.upper() in COINGECKO_IDS.keys():
        coin_id=COINGECKO_IDS[coin_id.upper()]
    cache_key = f"cg:coin:{coin_id}"
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    params = {
        "vs_currency": "usd",
        "ids": coin_id,
        "price_change_percentage": "1h,24h,7d,14d,30d,200d,1y",
    }
    try:
        response = await client.get(f"/coins/markets", params=params)
        response.raise_for_status()
        data = response.json()[0]
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Coin '{coin_id}' not found. Check coin name.",
            )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"CoinGecko API error: {e.response.text}",
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="CoinGecko service temporarily unavailable",
        )
    coin_data={
        "id":data["id"],
        "current_price":data["current_price"],
        "price_change_24h":data["price_change_24h"],
        "price_change_percentage_1h":data["price_change_percentage_1h_in_currency"],
        "price_change_percentage_24h":data["price_change_percentage_24h_in_currency"],
        "price_change_percentage_7d":data["price_change_percentage_7d_in_currency"],
        "price_change_percentage_14d":data["price_change_percentage_14d_in_currency"],
        "price_change_percentage_30d":data["price_change_percentage_30d_in_currency"],
        "price_change_percentage_200d":data["price_change_percentage_200d_in_currency"],
        "price_change_percentage_1y":data["price_change_percentage_1y_in_currency"],
        "last_updated":datetime.fromisoformat(data["last_updated"].replace("Z", "+00:00"))
    }

    await redis_client.setex(cache_key,120,json.dumps(coin_data,default=str))
    return coin_data


@router.get("/trending")
async def trending(client: HttpClient):
    cached = await redis_client.get("cg:trending")
    if cached:
        return json.loads(cached)

    response = await client.get("/search/trending")
    response.raise_for_status()
    raw = response.json()

    coins = []
    for entry in raw.get("coins", []):
        item = entry["item"]
        data = item.get("data", {})
        change = data.get("price_change_percentage_24h", {})

        coins.append({
            "id": item["id"],
            "symbol": item["symbol"].upper(),
            "price_usd": data.get("price"),
            "price_change_percentage_24h": change.get("usd") if isinstance(change, dict) else None,
            "market_cap": data.get("market_cap"),
            "total_volume": data.get("total_volume"),
        })

    result = {"coins": coins}
    await redis_client.setex("cg:trending", 300, json.dumps(result))
    return result

@router.get("/my_all_coins_changes",response_model=list[CoinDataResponse])
async def get_all_coins_changes(user:CurrentUser,client:HttpClient,session:SessionDep):
    cached = await redis_client.get(f"cg:{user.id}:all_coins_changes")
    if cached:
        return [CoinDataResponse.model_validate(item) for item in json.loads(cached)]
    result = await session.execute(select(BalanceModel).where(BalanceModel.user_id==user.id))
    user_balances = result.scalars().all()
    if not user_balances:
        raise HTTPException(404,"Balances not found")

    coins_ids = {b.currency for b in user_balances}
    params = {
        "vs_currency": "usd",
        "ids": ",".join(coins_ids)
    }
    try:
        response = await client.get("/coins/markets",params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(503,f"CoinGecko error: {e}")
    
    market_by_id = {coin["id"]: coin for coin in data}

    coins_data = []
    for balance in user_balances:
        market = market_by_id.get(balance.currency)
        if market is None:
            current_price = None
            last_updated = None
        else:
            current_price = Decimal(str(market["current_price"]))
            last_updated_raw = market.get("last_updated")
            last_updated = datetime.fromisoformat(last_updated_raw.replace("Z", "+00:00"))

        coins_data.append(
            _calculate_coin_data(balance, current_price, last_updated)
        )
    await redis_client.setex(f"cg:{user.id}:all_coins_changes",60,json.dumps([coin.model_dump(mode="json") for coin in coins_data]))
    return coins_data

@router.get("/see_coin_changes/{coin_id}",response_model=CoinDataResponse)
async def get_one_coin_changes(
        user:CurrentUser,
        client:HttpClient,
        session:SessionDep,
        coin_id:str = Path(...,description="coin name('bitcoin', 'ethereum',...)")
):
    if coin_id.upper() in COINGECKO_IDS.keys():
                coin_id=COINGECKO_IDS[coin_id.upper()]
    cached = await redis_client.get(f"cg:{user.id}:coin_changes:{coin_id}")
    if cached:
        return json.loads(cached)
    result = await session.execute(
        select(BalanceModel).where(
            BalanceModel.user_id == user.id,
            BalanceModel.currency==coin_id
        )
    )
    balance = result.scalar_one_or_none()
    if not balance:
        raise HTTPException(404, f"Balance with coin '{coin_id}' not found")
    params = {
        "vs_currency": "usd",
        "ids": coin_id
    }
    try:
        response = await client.get("/coins/markets",params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(503,f"CoinGecko error: {e}")

    current_price = Decimal(str(data[0].get("current_price")))
    last_updated_raw = data[0].get("last_updated")
    last_updated = datetime.fromisoformat(last_updated_raw.replace("Z", "+00:00"))
    
    coin_data = _calculate_coin_data(
        balance,
        current_price,
        last_updated    
    )
    await redis_client.setex(f"cg:{user.id}:coin_changes:{coin_id}",30,coin_data.model_dump_json())
    return coin_data

