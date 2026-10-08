# Cloud-Native Derivatives Processing System (Base Project)

A small microservice system for pricing derivatives and computing portfolio risk, built with
cloud-native practices: stateless services, 12-factor config, health probes, metrics, structured
logs, containers, and Kubernetes manifests with autoscaling.

## Architecture

```
                         ┌───────────────┐   ticks (Redis Stream "ticks", SSE /v1/stream)
                         │  marketdata   │────────────────────────────────────────┐
                         │  :8002        │                                        ▼
                         └──────▲────────┘                                  (other consumers)
                                │ GET /v1/prices
 client ──► ┌───────────────────┴──┐  POST /v1/batch   ┌────────────┐
            │  portfolio  :8003    │ ────────────────► │  pricing   │  stateless, CPU-bound
            │  trades · positions  │                   │  :8001     │  scales with HPA (2-10)
            │  · risk (Greeks, PnL)│                   └────────────┘
            └───────────┬──────────┘
                        │ append-only trade log
                        ▼
                     Redis (in-memory fallback for local dev)
```

| Service | Responsibility | Scaling |
|---|---|---|
| `pricing` | Black-Scholes price + Greeks (calls, puts, futures), batch pricing, implied vol | Stateless → many replicas |
| `marketdata` | Simulated GBM feed; REST snapshot, SSE stream, publishes to Redis Stream | 1 replica (in-process simulator); replace with a real feed handler in production |
| `portfolio` | Books trades (event-sourced log), derives net positions, valuations, portfolio Greeks and P&L | Stateless (state in Redis) → many replicas |

### Cloud-native features
- **12-factor config** via environment variables (`common/config.py`, K8s ConfigMap)
- **Health probes**: `/health/live`, `/health/ready` (portfolio readiness checks Redis)
- **Observability**: JSON logs with request-ID propagation across services; Prometheus `/metrics`
- **Containers**: slim, non-root images with HEALTHCHECK; `docker-compose.yml` for the local stack
- **Kubernetes**: Deployments, Services, ConfigMap, HPAs, resource requests/limits, probes, Kustomize
- **Resilience**: upstream failures return `503` (not a crash); slow SSE consumers are dropped, not blocking the feed
- **Event sourcing**: positions are derived from the trade log, so any replica can serve any request

## Quick start

### A) No Docker (fastest)
```bash
pip install -r requirements-dev.txt
pytest -q

# three terminals (from the project root)
uvicorn services.pricing.app:app --port 8001
uvicorn services.marketdata.app:app --port 8002
PRICING_URL=http://localhost:8001 MARKETDATA_URL=http://localhost:8002 \
  uvicorn services.portfolio.app:app --port 8003

python scripts/demo.py          # books 3 trades and prints live risk
```
Interactive API docs: http://localhost:8001/docs · :8002/docs · :8003/docs

### B) Docker Compose
```bash
docker compose up --build
python scripts/demo.py
```

### C) Kubernetes (kind / minikube / any cluster)
```bash
make build                         # builds derivatives-{pricing,marketdata,portfolio}:latest
kind load docker-image derivatives-pricing derivatives-marketdata derivatives-portfolio   # or push to a registry
kubectl apply -k k8s/
kubectl -n derivatives port-forward svc/portfolio 8003:8003
python scripts/demo.py
```
(HPAs need metrics-server installed in the cluster.)

## API examples
```bash
# price an option
curl -X POST localhost:8001/v1/price -H 'content-type: application/json' \
  -d '{"kind":"call","spot":100,"strike":100,"years":1,"rate":0.05,"vol":0.2}'

# book a trade
curl -X POST localhost:8003/v1/trades -H 'content-type: application/json' \
  -d '{"symbol":"AAPL-C-190","underlying":"AAPL","kind":"call","strike":190,"expiry_days":30,"quantity":20,"price":3.5}'

curl localhost:8003/v1/positions
curl localhost:8003/v1/risk
curl -N "localhost:8002/v1/stream?limit=5"     # live ticks (Server-Sent Events)
```

## Configuration (env vars)
`LOG_LEVEL`, `REDIS_URL`, `PRICING_URL`, `MARKETDATA_URL`, `SYMBOLS` (`AAPL:185,ES:5200`),
`VOLS` (`AAPL=0.28,ES=0.16`), `DEFAULT_VOL`, `RATE`, `DIV_YIELD`, `TICK_INTERVAL`, `ANNUAL_VOL`, `SEED`.

## Project layout
```
common/          shared library: config, observability, pricing math, schemas, position logic
services/        pricing/ marketdata/ portfolio/ (each: app.py + Dockerfile)
k8s/             Kubernetes manifests (kustomize)
tests/           API, end-to-end (in-process), Redis and manifest tests
scripts/demo.py  sample client
```

## Next steps toward production
- Replace the simulator with a real feed handler; swap Redis Streams for Kafka if needed
- Persist trades in Postgres; use a consumer group for downstream processors
- Add auth (OIDC/JWT), an Ingress/API gateway, and network policies
- Volatility surface instead of one vol per underlying; add VaR / stress tests
- Tracing with OpenTelemetry; Prometheus + Grafana dashboards; CI/CD (build, test, push, deploy)
