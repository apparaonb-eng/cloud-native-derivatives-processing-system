"""Trade-log storage. Redis in the cluster, in-memory for local dev/tests."""
from __future__ import annotations

import json
from typing import Protocol


class TradeStore(Protocol):
    async def add_trade(self, trade: dict) -> dict: ...
    async def list_trades(self) -> list[dict]: ...
    async def ping(self) -> bool: ...


class InMemoryStore:
    def __init__(self):
        self._trades: list[dict] = []

    async def add_trade(self, trade: dict) -> dict:
        trade = {**trade, "trade_id": len(self._trades) + 1}
        self._trades.append(trade)
        return trade

    async def list_trades(self) -> list[dict]:
        return list(self._trades)

    async def ping(self) -> bool:
        return True


class RedisStore:
    KEY, SEQ = "trades", "trades:seq"

    def __init__(self, client):
        self.r = client

    async def add_trade(self, trade: dict) -> dict:
        trade = {**trade, "trade_id": await self.r.incr(self.SEQ)}
        await self.r.rpush(self.KEY, json.dumps(trade))
        return trade

    async def list_trades(self) -> list[dict]:
        return [json.loads(x) for x in await self.r.lrange(self.KEY, 0, -1)]

    async def ping(self) -> bool:
        return bool(await self.r.ping())
