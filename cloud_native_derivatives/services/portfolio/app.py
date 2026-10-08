"""Portfolio & risk service: books trades, derives positions, values them via other services."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from common.config import Settings
from common.observability import configure_logging, install_observability, request_id_var
from common.portfolio import net_positions, years_to_expiry
from common.schemas import TradeIn
from .store import InMemoryStore, RedisStore, TradeStore

log = logging.getLogger("portfolio")


def create_app(settings: Settings | None = None, store: TradeStore | None = None,
               http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env("portfolio")
    configure_logging(settings.service_name, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis_client = None
        if store is not None:
            app.state.store = store
        elif settings.redis_url:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
            app.state.store = RedisStore(redis_client)
        else:
            log.warning("REDIS_URL not set: using in-memory store (data lost on restart)")
            app.state.store = InMemoryStore()
        app.state.http = httpx.AsyncClient(transport=http_transport, timeout=5.0)
        app.state.ready_check = app.state.store.ping
        yield
        await app.state.http.aclose()
        if redis_client is not None:
            await redis_client.aclose()

    app = FastAPI(title="Portfolio & Risk Service", version="1.0.0", lifespan=lifespan)
    install_observability(app, settings.service_name)

    @app.post("/v1/trades", status_code=201)
    async def book_trade(trade: TradeIn):
        return await app.state.store.add_trade(trade.to_record())

    @app.get("/v1/trades")
    async def trades():
        return await app.state.store.list_trades()

    @app.get("/v1/positions")
    async def positions():
        book = net_positions(await app.state.store.list_trades())
        return [p for p in book.values() if p["quantity"] != 0]

    async def _call(method: str, url: str, **kw):
        headers = {"x-request-id": request_id_var.get()}  # propagate trace id downstream
        try:
            resp = await app.state.http.request(method, url, headers=headers, **kw)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            log.error("upstream call failed", extra={"ctx": {"url": url, "error": str(exc)}})
            raise HTTPException(503, f"upstream unavailable: {url}")

    @app.get("/v1/risk")
    async def risk():
        book = net_positions(await app.state.store.list_trades())
        open_pos = [p for p in book.values() if p["quantity"] != 0]
        realized = sum(p["realized_pnl"] for p in book.values())
        empty = dict(market_value=0.0, unrealized_pnl=0.0, realized_pnl=realized, total_pnl=realized,
                     delta=0.0, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)
        if not open_pos:
            return {"valuations": [], "totals": empty}

        spots = (await _call("GET", f"{settings.marketdata_url}/v1/prices"))["prices"]
        items = []
        for p in open_pos:
            if p["underlying"] not in spots:
                raise HTTPException(503, f"no market data for {p['underlying']}")
            items.append({"kind": p["kind"], "spot": spots[p["underlying"]], "strike": p["strike"],
                          "years": years_to_expiry(p["expiry"]), "rate": settings.rate,
                          "vol": settings.vols.get(p["underlying"], settings.default_vol),
                          "div_yield": settings.div_yield})
        results = (await _call("POST", f"{settings.pricing_url}/v1/batch", json={"items": items}))["results"]

        vals, tot = [], dict(empty, realized_pnl=realized)
        for p, it, g in zip(open_pos, items, results):
            scale = p["quantity"] * p["multiplier"]
            v = {"symbol": p["symbol"], "quantity": p["quantity"], "spot": it["spot"], "price": g["price"],
                 "market_value": scale * g["price"],
                 "unrealized_pnl": scale * (g["price"] - p["avg_price"]),
                 **{k: scale * g[k] for k in ("delta", "gamma", "vega", "theta", "rho")}}
            vals.append(v)
            for k in ("market_value", "unrealized_pnl", "delta", "gamma", "vega", "theta", "rho"):
                tot[k] += v[k]
        tot["total_pnl"] = tot["unrealized_pnl"] + tot["realized_pnl"]
        return {"valuations": vals, "totals": tot}

    return app


app = create_app()
