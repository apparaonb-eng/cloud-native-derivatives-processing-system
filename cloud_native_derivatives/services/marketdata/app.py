"""Market data service: simulated GBM feed -> REST snapshot, SSE stream, optional Redis Stream.

NOTE: the simulator is in-process state, so run ONE replica. In production this service is
replaced by a feed handler publishing to a broker (Kafka / Redis Streams) that others consume.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from common.config import Settings
from common.observability import configure_logging, install_observability

log = logging.getLogger("marketdata")
TICK_STREAM = "ticks"
TIME_SCALE = 5000  # exaggerate time so demo moves are visible


class Feed:
    def __init__(self, settings: Settings):
        self.prices = dict(settings.symbols)
        self.interval = settings.tick_interval
        self.vol = settings.annual_vol
        self.rng = random.Random(settings.seed)
        self.subscribers: set[asyncio.Queue] = set()
        self.tick_count = 0

    def step(self) -> list[dict]:
        dt = self.interval / (252 * 6.5 * 3600) * TIME_SCALE
        ticks = []
        for sym, px in self.prices.items():
            z = self.rng.gauss(0, 1)
            px = px * math.exp(-0.5 * self.vol ** 2 * dt + self.vol * math.sqrt(dt) * z)
            self.prices[sym] = px
            ticks.append({"symbol": sym, "price": round(px, 4),
                          "ts": datetime.now(timezone.utc).isoformat()})
        self.tick_count += len(ticks)
        return ticks

    def fan_out(self, tick: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(tick)
            except asyncio.QueueFull:  # slow consumer: drop rather than block the feed
                pass


async def _feed_loop(feed: Feed, redis_client, metrics) -> None:
    while True:
        for tick in feed.step():
            feed.fan_out(tick)
            if redis_client is not None:
                try:
                    await redis_client.xadd(TICK_STREAM, tick, maxlen=10_000, approximate=True)
                except Exception:
                    log.warning("redis publish failed", exc_info=True)
        metrics.gauges["ticks_generated_total"] = feed.tick_count
        await asyncio.sleep(feed.interval)


def create_app(settings: Settings | None = None, redis_client=None, start_feed: bool = True) -> FastAPI:
    settings = settings or Settings.from_env("marketdata")
    configure_logging(settings.service_name, settings.log_level)
    feed = Feed(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client, owned = redis_client, False
        if client is None and settings.redis_url:
            import redis.asyncio as aioredis
            client, owned = aioredis.from_url(settings.redis_url, decode_responses=True), True
        task = asyncio.create_task(_feed_loop(feed, client, app.state.metrics)) if start_feed else None
        log.info("feed started", extra={"ctx": {"symbols": list(feed.prices)}})
        yield
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if owned:
            await client.aclose()

    app = FastAPI(title="Market Data Service", version="1.0.0", lifespan=lifespan)
    install_observability(app, settings.service_name)
    app.state.feed = feed

    @app.get("/v1/prices")
    async def prices():
        return {"ts": datetime.now(timezone.utc).isoformat(),
                "prices": {k: round(v, 4) for k, v in feed.prices.items()}}

    @app.get("/v1/prices/{symbol}")
    async def price(symbol: str):
        if symbol not in feed.prices:
            raise HTTPException(404, f"unknown symbol {symbol}")
        return {"symbol": symbol, "price": round(feed.prices[symbol], 4)}

    @app.get("/v1/stream")
    async def stream(limit: int | None = Query(default=None, ge=1, description="stop after N ticks")):
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        feed.subscribers.add(q)

        async def gen():
            sent = 0
            try:
                while limit is None or sent < limit:
                    tick = await q.get()
                    sent += 1
                    yield f"data: {json.dumps(tick)}\n\n"
            finally:
                feed.subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


app = create_app()
