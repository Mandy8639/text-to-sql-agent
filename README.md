# Text-to-SQL Analytics Agent

Ask a business question in plain English and get back SQL, a result table, a plain-English
explanation, and a chart. Built on the Olist Brazilian e-commerce dataset with PostgreSQL,
pgvector, and Claude.

> Status: **Phase 0 — project skeleton.** Later phases add data loading, the agent, the eval
> benchmark, safety, observability, the API/UI, and deployment.

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (Docker Desktop, OrbStack, or Colima), and
`make`.

```bash
make setup     # install deps, create .env from .env.example
# edit .env: set ANTHROPIC_API_KEY (and change POSTGRES_PASSWORD + DATABASE_URL to match)
make db-up     # start Postgres 16 + pgvector and wait until healthy
make test      # run tests
```

## Commands

| Command          | What it does                                               |
| ---------------- | ---------------------------------------------------------- |
| `make setup`     | `uv sync` and create `.env` if missing                     |
| `make db-up`     | Start Postgres + pgvector (waits for the healthcheck)      |
| `make db-down`   | Stop Postgres (keeps data)                                 |
| `make db-reset`  | Stop Postgres and delete its data volume                   |
| `make test`      | All tests; DB/LLM smoke tests skip if unavailable          |
| `make test-unit` | Only tests that need no services or API key                |
| `make lint`      | ruff lint + format check                                   |
| `make format`    | Auto-fix lint issues and format                            |
| `make run-api`   | FastAPI server *(Phase 6)*                                 |
| `make run-ui`    | Streamlit UI *(Phase 6)*                                   |

## Project layout

```
app/
  agent/       orchestration, prompts, self-correction loop
  retrieval/   schema + few-shot retrieval
  safety/      SQL validation and guardrails
  db/          connections, execution
  llm/         provider interface (base.py) + Anthropic implementation
  config.py    typed settings from environment / .env
api/           FastAPI
ui/            Streamlit
evals/         benchmark questions, harness, results
scripts/       data loading, indexing
tests/         unit/ (no services) and integration/ (Postgres, LLM)
docs/          architecture and decisions log
docker/        Postgres init scripts
```

## Configuration

All settings come from environment variables (see [`.env.example`](.env.example)):

| Variable            | Default            | Purpose                                     |
| ------------------- | ------------------ | ------------------------------------------- |
| `DATABASE_URL`      | local admin URL    | Admin connection for scripts                |
| `ANTHROPIC_API_KEY` | —                  | Claude API key                              |
| `LLM_MODEL`         | `claude-opus-5-5`  | Model ID                                    |
| `LLM_EFFORT`        | `medium`           | `low` / `medium` / `high` / `xhigh` / `max` |
| `LLM_MAX_TOKENS`    | `16000`            | Output token cap per call                   |

## Design decisions

See [docs/decisions.md](docs/decisions.md).
