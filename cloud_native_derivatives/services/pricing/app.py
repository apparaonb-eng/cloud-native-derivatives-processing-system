"""Pricing service: stateless, CPU-bound, horizontally scalable (see k8s HPA)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from common.config import Settings
from common.observability import configure_logging, install_observability
from common.pricing import bs_greeks, future_greeks, implied_vol
from common.schemas import (BatchRequest, BatchResponse, Greeks, ImpliedVolRequest,
                            PriceRequest)


def _price(req: PriceRequest) -> Greeks:
    if req.kind == "future":
        g = future_greeks(req.spot, req.years, req.rate, req.div_yield)
    else:
        g = bs_greeks(req.kind, req.spot, req.strike, req.years, req.rate, req.vol, req.div_yield)
    return Greeks(**g)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env("pricing")
    configure_logging(settings.service_name, settings.log_level)
    app = FastAPI(title="Pricing Service", version="1.0.0")
    install_observability(app, settings.service_name)

    @app.post("/v1/price", response_model=Greeks)
    async def price(req: PriceRequest):
        return _price(req)

    @app.post("/v1/batch", response_model=BatchResponse)
    async def batch(req: BatchRequest):
        return BatchResponse(results=[_price(i) for i in req.items])

    @app.post("/v1/implied-vol")
    async def iv(req: ImpliedVolRequest):
        try:
            vol = implied_vol(req.kind, req.market_price, req.spot, req.strike,
                              req.years, req.rate, req.div_yield)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"implied_vol": vol}

    return app


app = create_app()
