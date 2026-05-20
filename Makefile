# Common dev tasks. Tab-indented (Makefile requirement).
# Run `make help` for a list.

.PHONY: help up down logs migrate test lint format typecheck clean rebuild

COMPOSE := docker compose -f deploy/docker/compose.dev.yml

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up:  ## Bring up the dev stack
	$(COMPOSE) up -d
	@echo "Stack starting. Run 'make logs' to follow, or 'make migrate' once postgres is healthy."

down:  ## Tear down the dev stack
	$(COMPOSE) down

logs:  ## Tail logs from all services
	$(COMPOSE) logs -f

migrate:  ## Run database migrations
	POSTGRES_HOST=localhost \
	POSTGRES_PORT=5432 \
	POSTGRES_DB=gateway \
	POSTGRES_USER=gateway \
	POSTGRES_PASSWORD=gateway \
	alembic upgrade head

test:  ## Run all tests
	pytest

lint:  ## Run ruff lint checks
	ruff check .

format:  ## Auto-format with ruff
	ruff format .
	ruff check --fix .

typecheck:  ## Run mypy
	mypy libs/ services/

clean:  ## Remove containers, volumes, and build artifacts
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true

rebuild:  ## Rebuild service images
	$(COMPOSE) build --no-cache
