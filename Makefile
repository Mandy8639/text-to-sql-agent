.DEFAULT_GOAL := help
.PHONY: help setup db-up db-down db-reset load-data build-index profile-data eval eval-all retrieval-recall validate-gold test test-unit lint format run-api run-ui

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-17s %s\n", $$1, $$2}'

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

build-index: ## (Re)build the pgvector retrieval index (schema chunks + dev examples)
	uv run python -m scripts.build_index

profile-data: ## Print the data profile (row counts, date ranges, null rates) as Markdown
	uv run python -m scripts.profile_data

AGENT ?= baseline
SPLIT ?= test
eval: ## Evaluate an agent (make eval AGENT=baseline SPLIT=test); costs API credits
	uv run python -m evals.run_eval --agent $(AGENT) --split $(SPLIT)

eval-all: ## Evaluate every Phase 4 version on SPLIT (default test) with the current LLM_MODEL
	for a in baseline v2 v3 v4 v5; do uv run python -m evals.run_eval --agent $$a --split $(SPLIT) || exit 1; done

retrieval-recall: ## Offline schema-retrieval recall@k (no LLM calls); tune k on dev
	uv run python -m evals.retrieval_recall --split dev --ks 3 4 5 6

validate-gold: ## Run every gold query in evals/questions.yaml and show its result
	uv run python -m evals.validate_gold

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
