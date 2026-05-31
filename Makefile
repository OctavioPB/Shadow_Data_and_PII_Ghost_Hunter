.PHONY: dev stop build test lint lint-py lint-ts format format-py format-ts \
        migrate pre-commit-install help

# ─── Config ──────────────────────────────────────────────────────────────────
COMPOSE = docker compose
PYTHON  = python3
NPM     = npm --prefix dashboard

# ─── Dev ─────────────────────────────────────────────────────────────────────
dev: ## Bring up all services (Kafka, Airflow, PG, Redis, API, Dashboard)
	cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Services running:"
	@echo "  Dashboard  → http://localhost:5173"
	@echo "  API        → http://localhost:8000"
	@echo "  Airflow    → http://localhost:8080  (admin/admin)"
	@echo "  Kafka      → localhost:9092"
	@echo "  PostgreSQL → localhost:5432"
	@echo "  Redis      → localhost:6379"

stop: ## Stop and remove all containers
	$(COMPOSE) down

logs: ## Follow logs for all services
	$(COMPOSE) logs -f

# ─── Build ───────────────────────────────────────────────────────────────────
build: ## Build all Docker images
	$(COMPOSE) build

build-dashboard: ## Build dashboard for production
	$(NPM) run build

# ─── Test ────────────────────────────────────────────────────────────────────
test: test-py test-ts ## Run all tests (Python + TypeScript)

test-py: ## Run Python tests with coverage
	pytest --cov --cov-fail-under=80

test-ts: ## Run frontend tests with coverage
	$(NPM) run test:coverage

test-watch: ## Run frontend tests in watch mode
	$(NPM) run test:watch

# ─── Lint ────────────────────────────────────────────────────────────────────
lint: lint-py lint-ts ## Run all linters

lint-py: ## Lint Python (ruff + black)
	ruff check .
	black --check .

lint-ts: ## Lint TypeScript (eslint + tsc + prettier)
	$(NPM) run lint
	$(NPM) run format:check
	cd dashboard && npx tsc --noEmit

# ─── Format ──────────────────────────────────────────────────────────────────
format: format-py format-ts ## Auto-format all code

format-py: ## Format Python (ruff + black)
	ruff check --fix .
	black .

format-ts: ## Format TypeScript (prettier)
	$(NPM) run format

# ─── Database ────────────────────────────────────────────────────────────────
migrate: ## Run Alembic migrations
	alembic upgrade head

migrate-new: ## Create a new Alembic migration (usage: make migrate-new MSG="add foo table")
	alembic revision --autogenerate -m "$(MSG)"

# ─── Setup ───────────────────────────────────────────────────────────────────
install: ## Install all dependencies (Python + Node)
	pip install -r requirements-dev.txt
	$(NPM) install

pre-commit-install: ## Install pre-commit hooks
	pre-commit install
	pre-commit install --hook-type commit-msg

# ─── Help ────────────────────────────────────────────────────────────────────
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
