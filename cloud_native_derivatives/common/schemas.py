"""Shared request/response models (the API contract between services)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Kind = Literal["call", "put", "future"]


class PriceRequest(BaseModel):
    kind: Kind
    spot: float = Field(gt=0)
    strike: float | None = Field(default=None, gt=0)
    years: float = Field(ge=0, description="time to expiry in years")
    rate: float = 0.05
    vol: float = Field(default=0.25, gt=0)
    div_yield: float = 0.0

    @model_validator(mode="after")
    def _need_strike(self):
        if self.kind != "future" and self.strike is None:
            raise ValueError("strike is required for options")
        return self


class Greeks(BaseModel):
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


class BatchRequest(BaseModel):
    items: list[PriceRequest] = Field(max_length=1000)


class BatchResponse(BaseModel):
    results: list[Greeks]


class ImpliedVolRequest(BaseModel):
    kind: Literal["call", "put"]
    market_price: float = Field(gt=0)
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    years: float = Field(gt=0)
    rate: float = 0.05
    div_yield: float = 0.0


class TradeIn(BaseModel):
    symbol: str
    underlying: str
    kind: Kind
    strike: float | None = Field(default=None, gt=0)
    expiry: datetime | None = None
    expiry_days: float | None = Field(default=None, gt=0, description="convenience: days from now")
    quantity: int
    price: float = Field(ge=0)
    multiplier: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate(self):
        if self.quantity == 0:
            raise ValueError("quantity must be non-zero")
        if self.kind != "future" and self.strike is None:
            raise ValueError("strike is required for options")
        if self.expiry is None:
            if self.expiry_days is None:
                raise ValueError("provide expiry or expiry_days")
            self.expiry = datetime.now(timezone.utc) + timedelta(days=self.expiry_days)
        elif self.expiry.tzinfo is None:
            self.expiry = self.expiry.replace(tzinfo=timezone.utc)
        if self.multiplier is None:
            self.multiplier = 50 if self.kind == "future" else 100
        return self

    def to_record(self) -> dict:
        return {"symbol": self.symbol, "underlying": self.underlying, "kind": self.kind,
                "strike": self.strike, "expiry": self.expiry.isoformat(),
                "multiplier": self.multiplier, "quantity": self.quantity, "price": self.price}
