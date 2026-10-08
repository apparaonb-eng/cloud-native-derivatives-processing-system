"""Pure pricing functions (no I/O): Black-Scholes-Merton Greeks, implied vol, futures."""
from __future__ import annotations

import math


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_greeks(kind: str, s: float, k: float, t: float, r: float, vol: float, q: float = 0.0) -> dict[str, float]:
    """European option price and Greeks. vega per 1 vol-pt, theta per day, rho per 1% rate."""
    if t <= 1e-12:  # expired -> intrinsic value
        intrinsic = max(s - k, 0.0) if kind == "call" else max(k - s, 0.0)
        delta = (1.0 if s > k else 0.0) if kind == "call" else (-1.0 if s < k else 0.0)
        return dict(price=intrinsic, delta=delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)

    sq = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * vol * vol) * t) / (vol * sq)
    d2 = d1 - vol * sq
    dr, dq = math.exp(-r * t), math.exp(-q * t)
    gamma = dq * _pdf(d1) / (s * vol * sq)
    vega = s * dq * _pdf(d1) * sq
    base_theta = -(s * dq * _pdf(d1) * vol) / (2.0 * sq)

    if kind == "call":
        price = s * dq * _cdf(d1) - k * dr * _cdf(d2)
        delta = dq * _cdf(d1)
        theta = base_theta - r * k * dr * _cdf(d2) + q * s * dq * _cdf(d1)
        rho = k * t * dr * _cdf(d2)
    else:
        price = k * dr * _cdf(-d2) - s * dq * _cdf(-d1)
        delta = -dq * _cdf(-d1)
        theta = base_theta + r * k * dr * _cdf(-d2) - q * s * dq * _cdf(-d1)
        rho = -k * t * dr * _cdf(-d2)

    return dict(price=price, delta=delta, gamma=gamma, vega=vega / 100.0,
                theta=theta / 365.0, rho=rho / 100.0)


def future_greeks(s: float, t: float, r: float, q: float = 0.0) -> dict[str, float]:
    return dict(price=s * math.exp((r - q) * t), delta=1.0,
                gamma=0.0, vega=0.0, theta=0.0, rho=0.0)


def implied_vol(kind: str, market_price: float, s: float, k: float, t: float, r: float,
                q: float = 0.0, lo: float = 1e-4, hi: float = 5.0) -> float:
    """Bisection implied volatility."""
    if t <= 0:
        raise ValueError("cannot solve implied vol at expiry")
    f = lambda v: bs_greeks(kind, s, k, t, r, v, q)["price"] - market_price
    if f(lo) > 0 or f(hi) < 0:
        raise ValueError("price outside no-arbitrage bounds")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        val = f(mid)
        if abs(val) < 1e-10:
            return mid
        lo, hi = (mid, hi) if val < 0 else (lo, mid)
    return 0.5 * (lo + hi)
