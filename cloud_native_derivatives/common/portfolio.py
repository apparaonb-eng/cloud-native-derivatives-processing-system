"""Event-sourced positions: net positions are derived from the append-only trade log."""
from __future__ import annotations

from datetime import datetime, timezone

SECONDS_PER_YEAR = 365.0 * 24 * 3600
_INSTR_KEYS = ("symbol", "underlying", "kind", "strike", "expiry", "multiplier")


def apply_fill(pos: dict, qty: int, price: float) -> None:
    cur = pos["quantity"]
    if cur == 0 or (cur > 0) == (qty > 0):
        total = cur + qty
        pos["avg_price"] = (pos["avg_price"] * abs(cur) + price * abs(qty)) / abs(total)
        pos["quantity"] = total
        return
    closing = min(abs(qty), abs(cur))
    sign = 1 if cur > 0 else -1
    pos["realized_pnl"] += sign * closing * (price - pos["avg_price"]) * pos["multiplier"]
    new = cur + qty
    if new == 0:
        pos["avg_price"] = 0.0
    elif (new > 0) != (cur > 0):
        pos["avg_price"] = price
    pos["quantity"] = new


def net_positions(trades: list[dict]) -> dict[str, dict]:
    book: dict[str, dict] = {}
    for t in trades:
        pos = book.get(t["symbol"])
        if pos is None:
            pos = book[t["symbol"]] = {k: t[k] for k in _INSTR_KEYS}
            pos.update(quantity=0, avg_price=0.0, realized_pnl=0.0)
        apply_fill(pos, t["quantity"], t["price"])
    return book


def years_to_expiry(expiry_iso: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max((datetime.fromisoformat(expiry_iso) - now).total_seconds(), 0.0) / SECONDS_PER_YEAR
