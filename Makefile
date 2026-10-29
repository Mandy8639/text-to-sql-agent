.DEFAULT_GOAL := help
.PHONY: help setup db-up db-down db-reset load-data profile-data test test-unit lint format run-api run-ui

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install dependencies and create .env from the example if missing
	uv sync
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill in ANTHROPIC_API_KEY.")

db-up: ## Start Postgres + pgvector and wait until it is healthy
	docker compose up -d --wait

db-down: ## Stop Postgres (data volume is kept)
	docker compose down

db-reset: ## Stop Postgres AND delete its data volume
	docker compose down -v

load-data: ## (Re)load the Olist CSVs from data/raw/ and create the read-only agent role
	uv run python -m scripts.load_data

profile-data: ## Print the data profile (row counts, date ranges, null rates) as Markdown
	uv run python -m scripts.profile_data

test: ## Run all tests (integration/LLM tests skip if services or keys are missing)
	uv run pytest

test-unit: ## Run only tests that need no services
	uv run pytest -m "not integration and not llm"

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint issues and format code
	uv run ruff check --fix .
	uv run ruff format .

run-api: ## Start the FastAPI server (Phase 6)
	@echo "Not built yet — the API arrives in Phase 6." && exit 1

run-ui: ## Start the Streamlit UI (Phase 6)
	@echo "Not built yet — the UI arrives in Phase 6." && exit 1
