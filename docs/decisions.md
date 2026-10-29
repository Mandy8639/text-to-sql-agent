# Decisions Log

A running record of design choices, the alternatives considered, and why. Newest at the bottom.

---

## D1 — Language & dependency management: Python 3.12 + uv (Phase 0)

- **Options:** pip + venv, Poetry, uv.
- **Pick:** uv. One tool handles the Python version, the virtualenv, and a lockfile (`uv.lock`), and
  it is fast enough that `make setup` from a fresh clone takes seconds. The Python version is pinned in
  `.python-version`; `requires-python >= 3.11` matches the project brief.
- **Note:** the project is not packaged (`[tool.uv] package = false`). Code is imported from the repo
  root (`app.*`), which keeps the layout simple; packaging can come later if needed for deployment.

## D2 — Database: PostgreSQL 16 + pgvector in one container (Phase 0)

- **Options:** separate vector DB (Qdrant, Chroma) vs. pgvector inside the analytics DB.
- **Pick:** pgvector, using the official `pgvector/pgvector:pg16` image. One service to run, back up,
  and deploy. Schema/few-shot retrieval is a few hundred vectors, far below the point where a
  dedicated vector DB pays off.
- Healthcheck uses `pg_isready`; `make db-up` runs `docker compose up --wait` so commands only
  continue once the DB accepts connections. Data lives in a named volume (`pgdata`) and survives
  `make db-down`; `make db-reset` wipes it.

## D3 — DB access: SQLAlchemy 2 + psycopg 3 (Phase 0)

- SQLAlchemy gives connection pooling and a dialect-neutral API without forcing an ORM; the agent
  will run raw generated SQL via `text()`. psycopg 3 is the maintained Postgres driver.
- `pool_pre_ping` + a short connect timeout so a dead DB fails fast instead of hanging.

## D4 — LLM: Anthropic Claude behind a thin `LLMProvider` protocol (Phase 0)

- **Interface:** `complete(system, user) -> LLMResponse(text, model, tokens, latency, stop_reason)`.
  It is a `typing.Protocol`, so any class with that method works — including fakes in tests — and
  agent code never imports the `anthropic` SDK directly.
- **Kept deliberately narrow:** single-turn text in, text out. Multi-turn, tool use, or structured
  outputs get added only when a phase actually needs them.
- **Default model:** `claude-opus-5-5` (current Opus, $4 / $20 per 1M tokens), configurable via
  `LLM_MODEL`. Cheaper models (`claude-sonnet-5-5`, `claude-haiku-5-5`) are a one-line env change and
  a good Phase 4 experiment: measure accuracy-per-dollar instead of assuming.
- **Effort, not temperature:** current Claude models reject sampling params (`temperature` etc.),
  and Opus 5.5 always thinks adaptively. `LLM_EFFORT` (default `medium`) is the cost/quality knob.
- **Refusal fallback:** requests opt into server-side `fallbacks: "default"`, so if a safety
  classifier declines a legitimate request it is retried on Anthropic's recommended fallback model
  in the same call. A final `stop_reason == "refusal"` raises `LLMError`.
- Errors from the SDK are wrapped in `LLMError` with only the exception type, so no internals
  (and never the key) leak into logs or API responses.
- **Pricing table** (`app/llm/pricing.py`) lets evals report cost per question; unknown models
  report `None` instead of a misleading number.

## D5 — Config: pydantic-settings + `.env` (Phase 0)

- Typed settings with validation (e.g. `LLM_EFFORT` must be a valid level). The API key is a
  `SecretStr`, so it never shows up in `repr()` or logs. `.env.example` is committed; `.env` is
  git-ignored. `make setup` copies the example if `.env` is missing.

## D6 — Embeddings: deferred to Phase 4

- Anthropic does not offer an embedding model. Plan: a small `Embedder` interface with a local
  sentence-transformers default (free, offline) and Voyage AI as an optional swap. Decided when
  retrieval is built, so the choice can be measured.

## D7 — Tests & tooling (Phase 0)

- pytest with two markers: `integration` (needs Postgres) and `llm` (calls the paid API). Both
  **skip with a reason** when the service/key is unavailable, so `make test` always runs; use
  `make test-unit` for the service-free subset (this is what CI will run).
- ruff for both linting and formatting (one tool, fast).
- Dependencies are added in the phase that first uses them (sqlglot, FastAPI, Streamlit, Langfuse
  come later), so every dependency in `pyproject.toml` is actually exercised.
