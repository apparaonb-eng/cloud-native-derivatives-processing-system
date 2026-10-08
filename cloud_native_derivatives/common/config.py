"""12-factor configuration: everything comes from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_pairs(raw: str, sep: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        key, _, val = item.partition(sep)
        out[key.strip()] = float(val)
    return out


@dataclass(frozen=True)
class Settings:
    service_name: str = "service"
    log_level: str = "INFO"
    redis_url: str | None = None
    pricing_url: str = "http://pricing:8001"
    marketdata_url: str = "http://marketdata:8002"
    symbols: dict[str, float] = field(default_factory=lambda: {"AAPL": 185.0, "ES": 5200.0})
    tick_interval: float = 0.5
    annual_vol: float = 0.30
    seed: int | None = None
    default_vol: float = 0.25
    vols: dict[str, float] = field(default_factory=lambda: {"AAPL": 0.28, "ES": 0.16})
    rate: float = 0.05
    div_yield: float = 0.0

    @classmethod
    def from_env(cls, service_name: str) -> "Settings":
        e = os.environ
        d = cls()
        return cls(
            service_name=service_name,
            log_level=e.get("LOG_LEVEL", d.log_level),
            redis_url=e.get("REDIS_URL") or None,
            pricing_url=e.get("PRICING_URL", d.pricing_url),
            marketdata_url=e.get("MARKETDATA_URL", d.marketdata_url),
            symbols=_parse_pairs(e["SYMBOLS"], ":") if "SYMBOLS" in e else d.symbols,
            tick_interval=float(e.get("TICK_INTERVAL", d.tick_interval)),
            annual_vol=float(e.get("ANNUAL_VOL", d.annual_vol)),
            seed=int(e["SEED"]) if "SEED" in e else None,
            default_vol=float(e.get("DEFAULT_VOL", d.default_vol)),
            vols=_parse_pairs(e["VOLS"], "=") if "VOLS" in e else d.vols,
            rate=float(e.get("RATE", d.rate)),
            div_yield=float(e.get("DIV_YIELD", d.div_yield)),
        )
