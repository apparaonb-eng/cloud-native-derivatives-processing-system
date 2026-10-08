import asyncio
import math
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import fakeredis
import fakeredis.aioredis
import httpx
from fastapi.testclient import TestClient

from common.config import Settings
from common.portfolio import net_positions
from common.observability import Metrics
from services.marketdata.app import Feed, _feed_loop, create_app as create_marketdata
from services.portfolio.app import create_app as create_portfolio
from services.pricing.app import create_app as create_pricing

SETTINGS = Settings(pricing_url="http://pricing:8001", marketdata_url="http://marketdata:8002",
                    tick_interval=0.01, seed=1, vols={"AAPL": 0.28, "ES": 0.16})


class Router(httpx.AsyncBaseTransport):
    """Routes in-process HTTP calls to the right ASGI app by hostname."""
    def __init__(self, apps):
        self.t = {h: httpx.ASGITransport(app=a) for h, a in apps.items()}

    async def handle_async_request(self, request):
        transport = self.t.get(request.url.host)
        if transport is None:  # behaves like an unreachable service
            raise httpx.ConnectError(f"cannot reach {request.url.host}")
        return await transport.handle_async_request(request)


# ---------- pricing ----------
def test_pricing_known_value_and_batch():
    c = TestClient(create_pricing(SETTINGS))
    body = dict(kind="call", spot=100, strike=100, years=1, rate=0.05, vol=0.2)
    r = c.post("/v1/price", json=body).json()
    assert abs(r["price"] - 10.4506) < 1e-3 and 0.6 < r["delta"] < 0.7
    b = c.post("/v1/batch", json={"items": [body, {**body, "kind": "put"},
                                            dict(kind="future", spot=100, years=1, rate=0.05)]}).json()
    assert len(b["results"]) == 3
    assert abs(b["results"][2]["price"] - 100 * math.exp(0.05)) < 1e-9


def test_pricing_validation_and_implied_vol():
    c = TestClient(create_pricing(SETTINGS))
    assert c.post("/v1/price", json=dict(kind="call", spot=100, years=1)).status_code == 422
    price = c.post("/v1/price", json=dict(kind="put", spot=95, strike=100, years=0.25, vol=0.37)).json()["price"]
    iv = c.post("/v1/implied-vol", json=dict(kind="put", market_price=price, spot=95, strike=100, years=0.25)).json()
    assert abs(iv["implied_vol"] - 0.37) < 1e-6
    assert c.post("/v1/implied-vol", json=dict(kind="call", market_price=500, spot=95, strike=100, years=0.25)).status_code == 422


# ---------- ops endpoints ----------
def test_health_and_metrics():
    c = TestClient(create_pricing(replace(SETTINGS, service_name="pricing")))
    assert c.get("/health/live").status_code == 200
    assert c.get("/health/ready").status_code == 200
    r = c.post("/v1/price", json=dict(kind="future", spot=10, years=1), headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
    m = c.get("/metrics").text
    assert 'http_requests_total{service="pricing",method="POST",path="/v1/price",status="200"} 1' in m


# ---------- market data ----------
def test_marketdata_rest_and_stream():
    with TestClient(create_marketdata(SETTINGS)) as c:
        assert set(c.get("/v1/prices").json()["prices"]) == {"AAPL", "ES"}
        assert c.get("/v1/prices/NOPE").status_code == 404
        with c.stream("GET", "/v1/stream?limit=3") as s:
            lines = [l for l in s.iter_lines() if l.startswith("data:")]
        assert len(lines) == 3


def test_feed_publishes_to_redis_stream():
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        feed, metrics = Feed(SETTINGS), Metrics("marketdata")
        task = asyncio.create_task(_feed_loop(feed, r, metrics))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        entries = await r.xrange("ticks")
        return entries, metrics
    entries, metrics = asyncio.run(run())
    assert len(entries) >= 4 and {"symbol", "price", "ts"} <= set(entries[0][1])
    assert metrics.gauges["ticks_generated_total"] >= 4


# ---------- portfolio ----------
def make_portfolio(store=None):
    router = Router({"pricing": create_pricing(SETTINGS),
                     "marketdata": create_marketdata(SETTINGS, start_feed=False)})
    return create_portfolio(SETTINGS, store=store, http_transport=router)


def test_trade_booking_positions_and_risk_end_to_end():
    with TestClient(make_portfolio()) as c:
        assert c.get("/health/ready").status_code == 200
        assert c.get("/v1/risk").json()["valuations"] == []

        t1 = c.post("/v1/trades", json=dict(symbol="AAPL-C-190", underlying="AAPL", kind="call", strike=190,
                                            expiry_days=30, quantity=20, price=3.5))
        assert t1.status_code == 201 and t1.json()["trade_id"] == 1
        c.post("/v1/trades", json=dict(symbol="AAPL-P-180", underlying="AAPL", kind="put", strike=180,
                                       expiry_days=30, quantity=-15, price=2.0))
        c.post("/v1/trades", json=dict(symbol="ES-FUT", underlying="ES", kind="future",
                                       expiry_days=90, quantity=2, price=5200))
        assert len(c.get("/v1/positions").json()) == 3

        risk = c.get("/v1/risk").json()
        assert len(risk["valuations"]) == 3
        T = risk["totals"]
        assert T["delta"] > 0 and T["gamma"] != 0 and "total_pnl" in T
        # futures contribute exactly qty*multiplier delta (2 * 50 = 100)
        fut = next(v for v in risk["valuations"] if v["symbol"] == "ES-FUT")
        assert abs(fut["delta"] - 100) < 1e-9


def test_trade_validation():
    with TestClient(make_portfolio()) as c:
        bad = dict(symbol="X", underlying="U", kind="call", expiry_days=5, quantity=1, price=1)
        assert c.post("/v1/trades", json=bad).status_code == 422                 # missing strike
        assert c.post("/v1/trades", json={**bad, "strike": 1, "quantity": 0}).status_code == 422
        assert c.post("/v1/trades", json={**bad, "strike": 1, "expiry_days": None}).status_code == 422


def test_portfolio_with_redis_store_and_upstream_failure():
    from services.portfolio.store import RedisStore
    store = RedisStore(fakeredis.aioredis.FakeRedis(decode_responses=True))
    router = Router({"pricing": create_pricing(SETTINGS)})  # marketdata missing -> upstream error
    app = create_portfolio(SETTINGS, store=store, http_transport=router)
    with TestClient(app) as c:
        c.post("/v1/trades", json=dict(symbol="ES-FUT", underlying="ES", kind="future",
                                       expiry_days=10, quantity=1, price=5000))
        assert c.get("/v1/trades").json()[0]["symbol"] == "ES-FUT"   # persisted in Redis
        assert c.get("/v1/risk").status_code == 503


def test_position_netting_and_realized_pnl():
    exp = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    base = dict(symbol="X", underlying="U", kind="call", strike=100.0, expiry=exp, multiplier=100)
    book = net_positions([{**base, "quantity": 10, "price": 2.0}, {**base, "quantity": -4, "price": 3.0}])
    assert book["X"]["quantity"] == 6 and math.isclose(book["X"]["realized_pnl"], 400.0)
