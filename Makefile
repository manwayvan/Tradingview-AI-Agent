.PHONY: dev test smoke tunnel docker-dev install deploy-check verify-main help

PORT ?= 8000

help:
	@echo "Local-first workflow (no Netlify deploy needed):"
	@echo "  make install    — venv + pip install"
	@echo "  make dev        — run web app with hot reload"
	@echo "  make test       — full test suite"
	@echo "  make smoke      — quick HTTP checks (dev must be running)"
	@echo "  make tunnel     — HTTPS tunnel for TradingView webhooks"
	@echo "  make docker-dev — same as dev, in Docker"
	@echo "  make verify-main — confirm only origin/main exists"
	@echo "  make deploy-check — main-only + tests + docker build before production"
	@echo ""
	@echo "Docs: docs/LOCAL_DEVELOPMENT.md  docs/DEPLOYMENT.md  docs/LOVABLE.md  docs/BRANCH_POLICY.md"

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@test -f .env.local || cp .env.local.example .env.local
	@echo "Done. Run: source .venv/bin/activate && make dev"

dev:
	@chmod +x scripts/dev.sh scripts/tunnel.sh scripts/smoke-local.sh
	@./scripts/dev.sh

test:
	python3 -m pytest tests/ -q --ignore=tests/test_vendor_errors.py

smoke:
	@chmod +x scripts/smoke-local.sh
	@./scripts/smoke-local.sh

tunnel:
	@chmod +x scripts/tunnel.sh
	@./scripts/tunnel.sh

docker-dev:
	docker compose -f docker-compose.dev.yml up --build

verify-main:
	@chmod +x scripts/verify-main.sh
	@./scripts/verify-main.sh

deploy-check:
	@chmod +x scripts/deploy-check.sh scripts/verify-main.sh
	@./scripts/verify-main.sh
	@./scripts/deploy-check.sh
