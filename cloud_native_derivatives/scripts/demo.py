"""Book a sample portfolio and print live risk.  Usage: python scripts/demo.py [portfolio_url]"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8003"
TRADES = [
    dict(symbol="AAPL-C-190", underlying="AAPL", kind="call", strike=190, expiry_days=30, quantity=20, price=3.5),
    dict(symbol="AAPL-P-180", underlying="AAPL", kind="put", strike=180, expiry_days=30, quantity=-15, price=2.0),
    dict(symbol="ES-FUT", underlying="ES", kind="future", expiry_days=90, quantity=2, price=5200),
]

with httpx.Client(base_url=BASE, timeout=5) as c:
    for t in TRADES:
        r = c.post("/v1/trades", json=t)
        r.raise_for_status()
        print("booked trade", r.json()["trade_id"], t["symbol"], t["quantity"])
    for _ in range(5):
        T = c.get("/v1/risk").json()["totals"]
        print(f"delta={T['delta']:>9,.1f} gamma={T['gamma']:>7,.2f} vega={T['vega']:>9,.1f} "
              f"theta={T['theta']:>8,.1f} pnl={T['total_pnl']:>10,.2f}")
        time.sleep(1)
