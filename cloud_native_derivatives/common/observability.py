"""Structured JSON logs, request IDs, Prometheus metrics and health probes."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "ctx", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


class Metrics:
    """Tiny Prometheus text-format registry (no extra dependency)."""

    def __init__(self, service: str):
        self.service = service
        self.requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.dur_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.dur_count: dict[tuple[str, str], int] = defaultdict(int)
        self.gauges: dict[str, float] = {}

    def observe(self, method: str, path: str, status: int, seconds: float) -> None:
        self.requests[(method, path, status)] += 1
        self.dur_sum[(method, path)] += seconds
        self.dur_count[(method, path)] += 1

    def render(self) -> str:
        s = self.service
        lines = ["# TYPE http_requests_total counter"]
        for (m, p, st), n in sorted(self.requests.items()):
            lines.append(f'http_requests_total{{service="{s}",method="{m}",path="{p}",status="{st}"}} {n}')
        lines.append("# TYPE http_request_duration_seconds summary")
        for (m, p), total in sorted(self.dur_sum.items()):
            lbl = f'service="{s}",method="{m}",path="{p}"'
            lines.append(f"http_request_duration_seconds_sum{{{lbl}}} {total:.6f}")
            lines.append(f"http_request_duration_seconds_count{{{lbl}}} {self.dur_count[(m, p)]}")
        for name, val in sorted(self.gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f'{name}{{service="{s}"}} {val}')
        return "\n".join(lines) + "\n"


def install_observability(app: FastAPI, service: str) -> Metrics:
    """Adds middleware plus /health/live, /health/ready and /metrics."""
    metrics = Metrics(service)
    app.state.metrics = metrics
    app.state.ready_check = None  # optional async callable -> bool
    log = logging.getLogger("http")

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            if not path.startswith(("/health", "/metrics")):
                metrics.observe(request.method, path, status, elapsed)
                log.info("request", extra={"ctx": {"method": request.method, "path": request.url.path,
                                                   "status": status, "ms": round(elapsed * 1000, 2)}})
            request_id_var.reset(token)

    @app.get("/health/live", tags=["ops"])
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["ops"])
    async def ready():
        check = app.state.ready_check
        try:
            ok = await check() if check else True
        except Exception:
            ok = False
        return JSONResponse({"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
    async def prom():
        return metrics.render()

    return metrics
