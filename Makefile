.DEFAULT_GOAL := help
.PHONY: help setup requirements setup-app-db bootstrap stack-up stack-down space-image db-up db-down db-reset load-data build-index profile-data eval eval-all retrieval-recall validate-gold test test-unit lint format run-api run-ui

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

setup-app-db: ## Create the demo spend-counter table and its write-only role (APP_DATABASE_URL)
	uv run python -m scripts.app_state

bootstrap: ## Load data, build the index, create the spend table: everything a fresh DB needs
	$(MAKE) load-data build-index setup-app-db

stack-up: ## Build and start db + api + ui in Docker (http://127.0.0.1:8501); run bootstrap once first
	docker compose --profile app up -d --build --wait

stack-down: ## Stop the full Docker stack (keeps the data volume)
	docker compose --profile app down

space-image: ## Build the single-container image locally (API + UI)
	docker build -f docker/space.Dockerfile -t text2sql-space .

requirements: ## Regenerate ui/requirements.txt (what Streamlit Community Cloud installs) from uv.lock
	uv export --frozen --no-default-groups --group ui --no-hashes --no-emit-project -o ui/requirements.txt -q

build-index: ## (Re)build the pgvector retrieval index (schema chunks + dev examples)
	uv run python -m scripts.build_index

profile-data: ## Print the data profile (row counts, date ranges, null rates) as Markdown
	uv run python -m scripts.profile_data

AGENT ?= baseline
SPLIT ?= test
eval: ## Evaluate an agent (make eval AGENT=baseline SPLIT=test); costs API credits
	uv run python -m evals.run_eval --agent $(AGENT) --split $(SPLIT)

eval-all: ## Evaluate baseline and v2-v6 on SPLIT (default test) with the current LLM_MODEL
	for a in baseline v2 v3 v4 v5 v6; do uv run python -m evals.run_eval --agent $$a --split $(SPLIT) || exit 1; done

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

run-api: ## Start the FastAPI server on :8000 (docs at /docs)
	uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

run-ui: ## Start the Streamlit UI on :8501 (needs run-api)
	uv run streamlit run ui/streamlit_app.py --server.port 8501 --server.address 127.0.0.1
