.PHONY: install test up down build k8s-apply run-local
install:      ; pip install -r requirements-dev.txt
test:         ; pytest -q
build:        ; for s in pricing marketdata portfolio; do docker build -f services/$$s/Dockerfile -t derivatives-$$s:latest .; done
up:           ; docker compose up --build
down:         ; docker compose down
k8s-apply:    ; kubectl apply -k k8s/
run-local:    ## three terminals, no Docker needed
	@echo "uvicorn services.pricing.app:app --port 8001"
	@echo "uvicorn services.marketdata.app:app --port 8002"
	@echo "PRICING_URL=http://localhost:8001 MARKETDATA_URL=http://localhost:8002 uvicorn services.portfolio.app:app --port 8003"
