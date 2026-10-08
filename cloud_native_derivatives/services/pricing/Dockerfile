# Build from the repository root:  docker build -f services/pricing/Dockerfile -t derivatives-pricing .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY services/__init__.py ./services/__init__.py
COPY services/pricing ./services/pricing
RUN useradd --system --uid 10001 appuser
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/health/live').status==200 else 1)"
CMD ["uvicorn", "services.pricing.app:app", "--host", "0.0.0.0", "--port", "8001"]
