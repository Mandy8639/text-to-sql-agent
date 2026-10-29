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

## D8 — Load pattern: COPY into all-TEXT staging, then transform in SQL (Phase 1)

- **Options:** pandas `read_csv` + `to_sql`; COPY straight into typed tables; COPY into TEXT
  staging, then `INSERT ... SELECT` with casts (ELT).
- **Pick:** ELT. `COPY` is the fastest way into Postgres (full load about 8 s), loading as TEXT
  means a bad value can never abort the copy, and every cleaning rule is one readable SQL
  statement in `scripts/sql/olist_schema.sql`. No pandas dependency.
- The whole load (staging, transform, role grants) is **one transaction**. If any step fails,
  the previous `olist` schema is untouched. Re-running rebuilds from the CSVs, so the load is
  reproducible.
- Constraints (PK/FK/CHECK) are declared in the DDL, so bad data fails loudly at load time
  instead of producing quietly wrong answers later.

## D9 — Keep original column names, fix only typos (Phase 1)

- Renaming everything (e.g. `order_purchase_timestamp` → `purchased_at`) would read nicer, but
  the original names are documented across Kaggle and are familiar to LLMs, which helps SQL
  generation. Only `product_name_lenght` / `product_description_lenght` were renamed.

## D10 — Collapse geolocation to one row per zip prefix (Phase 1)

- The raw table (1M rows, ~53 points per prefix) has no key, and joining it to customers
  multiplies rows: an inflated SUM that *looks* plausible, the worst kind of bug for an
  analytics agent. One row per prefix (mean in-Brazil coordinates, most common unaccented city
  and state) makes the join safe. The cost is that point-level detail is lost; no analytics
  question here needs it.
- Customer/seller → geolocation is documented as a relationship but **not** enforced as a FK,
  because 278 customer and 7 seller prefixes have no geolocation row.

## D11 — English category denormalized onto products (Phase 1)

- Saves the agent a join it would otherwise need in almost every category question. The
  translation table is kept for completeness. The 2 categories missing from the translation
  file were added (`pc_gamer`, `portable_kitchen_and_food_preparers`), so the FK holds.
- NULL categories stay NULL (not `'unknown'`) to be honest about missing data. schema.yaml tells
  the agent how to report them.

## D12 — Read-only agent role, with privileges as the real guard (Phase 1)

- `olist_agent`: SELECT on `olist` only; `REVOKE ALL ON DATABASE ... FROM PUBLIC` removes the
  default CREATE/TEMP rights; `REVOKE ALL ON SCHEMA public FROM PUBLIC`; NOSUPERUSER etc.; role
  defaults `default_transaction_read_only = on`, `statement_timeout = 30s`, `search_path = olist`.
- **Lesson from testing:** `default_transaction_read_only` is only a default. Any session can
  run `SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE`. The test proves writes still fail
  with `insufficient_privilege` (42501), because **privileges** are what actually stop them. The
  same test showed that a pooled connection keeps such session changes when it is reused, so the
  Phase 5 executor will start every transaction with `SET TRANSACTION READ ONLY` and its own
  timeout, rather than trusting the session.
- Not yet blocked: reading system catalogs (`pg_catalog`, `information_schema`) is allowed for
  every Postgres role. Phase 5's SQL validator blocks those queries.
- The role's password comes from `AGENT_DATABASE_URL`, so `.env` is the single source of truth.

## D13 — schema.yaml is tested against the database (Phase 1)

- Documentation that drifts from the schema silently degrades the agent's prompts. Tests check
  that every DB column is documented (and vice versa), with matching types, nullability and row
  counts, and that every column has a description and examples.
- Business rules (revenue definition, status filters, join fan-out, data coverage) live in
  `business_notes`, because most text-to-SQL errors are about meaning, not syntax. Open questions
  are listed under `ambiguities` with the current assumption, so gold SQL in Phase 3 can be
  consistent.

## D14 — Baseline prompt: full schema doc, including business rules (Phase 2)

- The baseline sends all of schema.yaml (about 6.2k tokens), rendered as compact text, on every
  question. "Naive" here means no retrieval, few-shot examples or retries. It does not mean a
  bad prompt: the business rules (revenue definition, status filters, data coverage) are part of
  the schema doc, so they are included. This makes later gains attributable to the techniques
  themselves, not to a deliberately weak starting point.
- Rendering is deterministic (byte-identical prompt for every question), which keeps runs
  reproducible and makes prompt caching possible later. Caching is deferred: it changes cost,
  not accuracy. At about $0.03 per question it is not worth the extra pricing logic yet.

## D15 — Prompts are versioned files, never edited after use (Phase 2)

- `app/agent/prompts/<name>_v<N>.md`, loaded with `string.Template` (`$schema`), because SQL is
  full of `{}` that would break `str.format`. The agent version string (`baseline-v1`) includes
  the prompt version, and eval results will record it, so every score maps to the exact prompt
  text that produced it. A prompt change means a new file and a new version.

## D16 — SQL extraction: first fenced block, bare-SELECT fallback (Phase 2)

- The prompt asks for one ```` ```sql ```` block; the extractor takes the first fenced block (any
  or no language tag), or the whole reply if it starts with SELECT/WITH. It does **not** try to
  validate or split statements; that is the safety layer's job (Phase 5). Prose outside the
  block is kept as a `note`, because the model uses it to state assumptions and caveats
  (e.g. "September 2018 is incomplete").
- Considered: structured outputs / tool use to force a JSON `{sql, note}` reply. That is more
  robust, but adds a moving part to the baseline; we can measure it later as its own step
  if extraction failures show up in evals.

## D17 — Executor: per-transaction READ ONLY + timeout, capped fetch, errors as values (Phase 2)

- Every query runs in a transaction that starts with `SET TRANSACTION READ ONLY` and
  `SET LOCAL statement_timeout`, and is always rolled back. This applies the D12 lesson: never
  trust session state on a pooled connection. A test tampers with a pooled connection and
  proves the next query is still read-only.
- Fetches at most `max_rows + 1` rows, so a huge result can't exhaust memory, and reports
  `truncated`. Phase 5 adds an automatic LIMIT in the SQL itself.
- Postgres errors become `QueryExecutionError(message, sqlstate)`, with only the first line of
  the server message (e.g. `column "x" does not exist`). That is useful for self-correction in
  Phase 4 and leaks no connection details.
- The agent returns failures in `AgentResult.error_stage` instead of raising, so the eval
  harness can count *where* the pipeline breaks (`llm` / `extract` / `execute`).

## D18 — Observed baseline behaviour to measure in Phase 3 (Phase 2)

- Unanswerable question ("profit margin per seller"): the model explains in its note that
  there is no cost data, but still returns substitute SQL. No refusal path exists yet.
- Destructive request ("delete all canceled orders"): it writes a SELECT listing the rows
  instead of a DELETE. Safe (and the DB would reject a DELETE anyway), but not a refusal.
- Both should count as **wrong** on refusal-type benchmark questions, and are the target of v5.
