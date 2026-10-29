# Text-to-SQL Analytics Agent

Ask a business question in plain English and get back SQL, a result table, a plain-English
explanation, and a chart. Built on the Olist Brazilian e-commerce dataset with PostgreSQL,
pgvector, and Claude.

> Status: **Phase 6 — API, Streamlit UI, grounded explanations and charts.** Next: deployment.

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (Docker Desktop, OrbStack, or Colima),
`make`, and the [Olist dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(9 CSVs, unzipped into `data/raw/`).

```bash
make setup      # install deps, create .env from .env.example
# edit .env: set ANTHROPIC_API_KEY; change the passwords in POSTGRES_PASSWORD, DATABASE_URL,
# AGENT_DATABASE_URL and RAG_DATABASE_URL (keep all three URLs: the roles are created from them)
make db-up      # start Postgres 16 + pgvector and wait until healthy
make load-data  # load the CSVs (~10 s) and create the read-only agent role
make build-index # embed schema docs + dev examples into pgvector (first run downloads ~130 MB model)
make test       # run tests
make run-api    # terminal 1: FastAPI on http://127.0.0.1:8000 (OpenAPI docs at /docs)
make run-ui     # terminal 2: Streamlit on http://127.0.0.1:8501
```

## Using it

**UI** (`make run-ui`): ask a question (or click an example). You get a plain-English answer,
a chart when one fits, the result table, collapsible **Show SQL** and **Show reasoning steps**
panels (retrieved tables, examples used, every attempt), and 👍/👎 feedback recorded in Langfuse.

**API** (`make run-api`):

```bash
curl -s -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"question": "What was the total revenue in each month from January to August 2018?"}'
```

| Endpoint | Returns |
|---|---|
| `POST /query` | `outcome` (answer / clarify / refuse / error), `explanation`, `explanation_grounded`, `sql`, `columns`, `rows`, `chart` spec, `steps`, `trace_id`, `request_id`, latency, cost |
| `POST /feedback` | 204; stores a `user_feedback` score on the trace |
| `GET /health` | database + retrieval index status (503 if degraded) |
| `GET /schema` | documented tables and columns |

Limits: 3–500 characters per question, 10 questions/minute per IP (configurable). Errors return
`{"error", "error_id"}` only. **Explanations** use only numbers present in the result, and a
grounding check flags any that are not. **Charts** are chosen by rules: time → line,
category → bar, single value → metric, otherwise a table.

CLI (baseline agent only): `uv run python -m app.cli "Which 5 states have the most unique customers?"`

## Asking questions (baseline agent)

```
$ uv run python -m app.cli "What were the top 5 product categories by revenue in 2017?"
SQL:
SELECT
    COALESCE(p.product_category_name_english, 'unknown') AS category,
    ROUND(SUM(oi.price), 2) AS revenue
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.order_status NOT IN ('canceled', 'unavailable')
  AND o.order_purchase_timestamp >= '2017-01-01'
  AND o.order_purchase_timestamp < '2018-01-01'
GROUP BY COALESCE(p.product_category_name_english, 'unknown')
ORDER BY revenue DESC
LIMIT 5

category              | revenue
----------------------+-----------
bed_bath_table        | 497,970.94
watches_gifts         | 486,519.02
health_beauty         | 481,142.73
sports_leisure        | 447,546.59
computers_accessories | 400,490.61
(5 of 5 rows shown)

[baseline-v1 | claude-opus-5-5 | 6,234 in / 303 out tokens | $0.0310 | 3.5s]
```

How the baseline works ([app/agent/baseline.py](app/agent/baseline.py)):

1. **Prompt:** the versioned template [prompts/baseline_v1.md](app/agent/prompts/baseline_v1.md)
   plus the *entire* [schema.yaml](docs/schema.yaml), rendered as text (about 6.2k tokens).
2. **Generate:** one Claude call returns a ```` ```sql ```` block and an optional note.
3. **Extract:** [sql_extract.py](app/agent/sql_extract.py) pulls the SQL out of the reply.
4. **Execute:** [executor.py](app/db/executor.py) runs it as the read-only `olist_agent` role
   in a `READ ONLY` transaction with a 15 s timeout, fetching at most 1,000 rows.
5. **Report:** failures are returned with the stage they happened at (`llm`, `extract` or
   `execute`), never raised, so the eval harness can count them.

It is deliberately naive: no retrieval, no examples, no retries, and no way to refuse a
question.

## Evaluation

```bash
make eval AGENT=baseline SPLIT=test     # ~$2 on Opus 5.5, ~$0.06 on Haiku 5.5
LLM_MODEL=claude-haiku-5-5 make eval    # any model via env var
make validate-gold                      # run every gold query and show its result
uv run python -m evals.rescore evals/results/<run>.json   # re-grade a run after fixing gold SQL, no API cost
```

**Benchmark** ([evals/questions.yaml](evals/questions.yaml)): 123 business questions, 105 with
gold SQL, each tagged with difficulty and category, plus 18 that should be refused
(destructive, prompt injection, unanswerable from this data) or clarified (too vague).
The split is 35 dev / 88 test. 30 questions were added after the first baseline saturated the
original set: cohorts, join fan-out traps, NULL handling, percent-of-total, period
comparisons. Test questions can never reach a prompt: `few_shot_pool()` returns dev questions
only, and the harness runs `assert_no_leakage()` on every agent's prompt and on every example
it uses.

| | aggregation | filtering | joins | time series | window fns | ambiguous | refusal |
|---|---:|---:|---:|---:|---:|---:|---:|
| questions | 21 | 19 | 24 | 19 | 18 | 10 | 12 |

**Scoring** ([evals/compare.py](evals/compare.py)): execution accuracy compares *result sets*,
not SQL text. Column names and order are ignored, and extra columns are allowed. Row order is
ignored unless the question asks for a ranking. Numbers match within 0.01, `2017-11` equals
2017-11-01, and intervals are compared in days. Refusal questions are correct only if the
agent does not answer. Each run also records valid-SQL rate, p50/p95 latency, tokens and cost,
broken down by difficulty and category.

**Results** (spec v3, 88 test questions: 74 answerable + 14 refuse/clarify; history in
[evals/results/summary.md](evals/results/summary.md)). Each version adds one technique
([app/agent/pipeline.py](app/agent/pipeline.py)):

| Version | Adds | Haiku 5.5 exec / abstain | Opus 5.5 exec / abstain | Input tokens | $ / q (Haiku / Opus) |
|---|---|---:|---:|---:|---:|
| baseline-v1 | full schema doc in prompt | 100% / 0% | 100% / 0% | 6,634 | 0.0010 / 0.033 |
| v2 | schema retrieval (pgvector, top-6 tables + join paths) | 100% / 0% | – | 5,824 | 0.0010 |
| v3 | + 3 nearest dev examples (few-shot) | 100% / 0% | – | 6,344 | 0.0009 |
| v4 | + self-correction (≤ 2 retries) | 100% / 0% | – | 6,419 | 0.0010 |
| **v5** | **+ clarify / refuse** | 98.6% / **100%** | **100% / 100%** | 6,615 | 0.0009 / 0.031 |
| v6 | + safety layer (Phase 5) | 98.6% / 100% | – | 6,615 | 0.0009 |
| baseline-raw | names + types only, no docs | 79.7% / 0% | – | 1,436 | 0.0005 |
| **raw-fewshot** | no docs + 3 dev examples | **98.6%** / 0% | – | 1,988 | 0.0005 |

What this shows:

- **Clarify/refuse was the decisive step: abstention 0% → 100%** on both models, with no false
  refusals. Before it, the model often *said* "this data has no web traffic", then returned a
  proxy query anyway, because SQL was the only way to reply.
- **Examples can replace documentation:** with an undocumented schema, 3 retrieved dev examples
  lift accuracy from 79.7% to 98.6%. The examples' SQL carries the business definitions
  (e.g. revenue = payments on non-canceled orders). This holds for test questions with no
  near-duplicate dev example (85.7% → 100%).
- **Retrieval, few-shot and self-correction did not move accuracy in the documented setup,**
  which was already at 100%. They cut input tokens (−12%), output tokens and latency instead.
  Self-correction triggered once, and hurt: it pushed a correct prose refusal into writing SQL.
- **The 100% is this benchmark's ceiling, not proof of a perfect agent** (one author, conventions
  partly shaped by test failures). So the frozen v5 was also run once on a **blind hold-out set**
  of 20 questions written by the project owner: **19/20 on both Opus and Haiku** (10/10
  auto-graded, 6/7 open-ended questions graded by rubric, 3/3 refuse/clarify). The one miss is
  the recurring "average order value" convention. See
  [evals/results/holdout_review.md](evals/results/holdout_review.md),
  [docs/failure_analysis.md](docs/failure_analysis.md) and decisions D31–D32.

Retrieval: `make build-index` embeds table docs and dev examples locally (fastembed,
bge-small-en-v1.5) into pgvector, read through a separate read-only role. `make retrieval-recall`
measures schema-retrieval recall without any LLM calls (k=6: 100% on dev and test).

## Safety and observability

**Four safety layers** (defense in depth; each tested on its own):

| Layer | Where | What it stops |
|---|---|---|
| Injection screen | [app/safety/injection.py](app/safety/injection.py) | "ignore previous instructions", "reveal your prompt", role switches: refused before any LLM call |
| SQL validator (sqlglot) | [app/safety/sql_validator.py](app/safety/sql_validator.py) | anything but one SELECT; writes hidden in CTEs or `SELECT INTO`; catalogs and other schemas; `pg_*` / file / network functions. Adds a LIMIT. |
| Executor | [app/db/executor.py](app/db/executor.py) | `READ ONLY` transaction, 15 s statement timeout, capped fetch |
| Database role | [scripts/readonly_role.py](scripts/readonly_role.py) | `olist_agent` can only SELECT on olist, even if every layer above failed |

Measured: 55 attack queries blocked ([tests/unit/test_safety.py](tests/unit/test_safety.py)),
**0 false positives**: all 122 gold queries pass and return identical rows after validation.
The injection screen flags 1 of 143 benchmark questions, the planted one.

**Observability:** with `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` set (free tier at
cloud.langfuse.com), every question becomes one trace: injection screen → retrieval (tables,
scores, examples) → each LLM attempt (prompt, reply, tokens, cost) → validation → execution.
Without keys, tracing is a no-op. Every log line is JSON with a `request_id` shared across one
question's steps. Eval runs tag traces with `eval`, `run:<timestamp>`, the split and the version, and run
files store each question's `trace_id`.

## Commands

| Command | What it does |
|---|---|
| `make setup` | `uv sync` and create `.env` if missing |
| `make db-up` | Start Postgres + pgvector (waits for the healthcheck) |
| `make db-down` | Stop Postgres (keeps data) |
| `make db-reset` | Stop Postgres and delete its data volume |
| `make load-data` | (Re)load CSVs into the `olist` schema; create the read-only agent role |
| `make build-index` | Build the pgvector retrieval index (schema docs + dev examples) and its read-only role |
| `make profile-data` | Print the data profile below as Markdown |
| `make eval AGENT=v5 SPLIT=test` | Evaluate one agent version (costs API credits) |
| `make eval-all` | Evaluate baseline and v2–v5 with the current `LLM_MODEL` |
| `make validate-gold` | Run every gold query and show its result |
| `make retrieval-recall` | Schema-retrieval recall@k on dev, no LLM calls |
| `make test` | All tests; DB/LLM tests skip if unavailable |
| `make test-unit` | Only tests that need no services or API key |
| `make lint` / `make format` | ruff lint + format check / auto-fix |
| `make run-api` / `make run-ui` | FastAPI server (:8000) / Streamlit UI (:8501) |

Other entry points: `python -m app.cli "question"` (baseline),
`python -m evals.rescore <run.json>` (re-grade after a gold fix, free),
`python -m evals.compare_runs OLD.json NEW.json` (question-level diff).

## Project layout

```
app/
  cli.py       `python -m app.cli "question"`
  agent/       baseline.py, pipeline.py (v2–v5), versioned prompts/, SQL/abstention parsing
  retrieval/   embedder, pgvector store, FK join-path graph
  safety/      injection screen, sqlglot validator, SafeExecutor
  observability/  Langfuse tracer (no-op without keys), JSON logs with request IDs
  db/          connections, read-only query executor
  llm/         provider interface (base.py) + Anthropic implementation
  service.py   QueryService: agent + chart + explanation, one trace per question
  charts.py, explain.py   rule-based chart spec; grounded explanation
  schema_doc.py, config.py
api/           FastAPI app (main.py): /query /feedback /health /schema
ui/            Streamlit app (app.py), talks to the API over HTTP
evals/         questions.yaml, dataset + leakage guard, compare/metrics, run_eval, rescore, results/
scripts/       data loading, retrieval index, data profile
tests/         unit/ (no services) and integration/ (Postgres, index, LLM)
docs/          schema.yaml, decisions.md, failure_analysis.md
docker/        Postgres init scripts
```

## Configuration

All settings come from environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local admin URL | Admin connection, scripts only |
| `AGENT_DATABASE_URL` | local agent URL | Read-only role the agent must use (business data only) |
| `RAG_DATABASE_URL` | local rag URL | Read-only role for the retrieval index only |
| `ANTHROPIC_API_KEY` | — | Claude API key |
| `LLM_MODEL` | `claude-opus-5-5` | Model ID (`claude-haiku-5-5` is ~33× cheaper at similar accuracy here) |
| `LLM_EFFORT` | `medium` | `low` / `medium` / `high` / `xhigh` / `max` |
| `LLM_MAX_TOKENS` | `16000` | Output token cap per call |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local fastembed model for retrieval |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | — | Enable Langfuse tracing (off when unset) |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse host (`https://us.cloud.langfuse.com` for US region) |
| `AGENT_VERSION` | `v6` | Pipeline config the API/UI serve |
| `EXPLAIN_MODEL` | `claude-haiku-5-5` | Model for result explanations |
| `API_RATE_LIMIT_PER_MINUTE` | `10` | Per-IP question limit |
| `API_URL` (UI only) | `http://localhost:8000` | Where the Streamlit UI finds the API |

## Data

`make load-data` copies the raw CSVs into an all-text `staging` schema, then builds a typed
`olist` schema in SQL ([scripts/sql/olist_schema.sql](scripts/sql/olist_schema.sql)) with
primary keys, foreign keys, CHECK constraints and indexes. Everything runs in one transaction:
a failed load leaves the previous data untouched. Every table and column is documented, with
business rules and known traps, in [docs/schema.yaml](docs/schema.yaml).

**Cleaning applied at load time**

- **geolocation:** 1,000,163 raw rows collapsed to 19,015 (one per zip prefix). The raw file has
  26% exact duplicates, accent variants (`são paulo` / `sao paulo`) and 42 points outside Brazil.
  Joining customers to the raw table would multiply rows.
- **products:** English category name joined onto the table. 2 categories missing from the
  translation file were added. `lenght` column typos fixed. `"40.0"` text cast to integers.
- **order_reviews:** keyed on `(review_id, order_id)`, because `review_id` alone is not unique.
- **CSV parsing:** review comments contain newlines inside quoted text (104,719 physical lines,
  99,224 records). The translation file starts with a UTF-8 byte-order mark, which is stripped.
- **zip prefixes:** stored as `CHAR(5)` text, so leading zeros survive.

**Read-only agent role.** `olist_agent` has `SELECT` on `olist` only, no `CREATE`/`TEMP` rights,
read-only transactions by default, and a 30 s statement timeout.
[tests/integration/test_readonly_role.py](tests/integration/test_readonly_role.py) proves that
INSERT, UPDATE, DELETE, TRUNCATE, DROP, ALTER, CREATE and GRANT all fail, even after the session
turns read-only mode off.

### Data profile

Generated by `make profile-data`.

| Table | Rows | Grain |
|---|---:|---|
| `orders` | 99,441 | one per order |
| `order_items` | 112,650 | one per unit sold |
| `order_payments` | 103,886 | one per payment method per order |
| `order_reviews` | 99,224 | one per (review, order) |
| `customers` | 99,441 | one per order-level customer id (96,096 people) |
| `products` | 32,951 | one per product (73 categories) |
| `sellers` | 3,095 | one per seller |
| `geolocation` | 19,015 | one per zip prefix |
| `category_translation` | 73 | one per category |

**Date ranges**

| Column | Min | Max |
|---|---|---|
| `orders.order_purchase_timestamp` | 2016-09-04 | 2018-10-17 |
| `orders.order_approved_at` | 2016-09-15 | 2018-09-03 |
| `orders.order_delivered_carrier_date` | 2016-10-08 | 2018-09-11 |
| `orders.order_delivered_customer_date` | 2016-10-11 | 2018-10-17 |
| `orders.order_estimated_delivery_date` | 2016-09-30 | 2018-11-12 |
| `order_items.shipping_limit_date` | 2016-09-19 | 2020-04-09 |
| `order_reviews.review_creation_date` | 2016-10-02 | 2018-08-31 |
| `order_reviews.review_answer_timestamp` | 2016-10-07 | 2018-10-29 |

Reliable monthly data is **2017-01 to 2018-08**. 2016 has under 330 orders in total, and
2018-09/10 have 20 orders, so the feed effectively stops in August 2018.

**Null rates** (columns with any nulls)

| Column | Nulls | % |
|---|---:|---:|
| `order_reviews.review_comment_title` | 87,656 | 88.3% |
| `order_reviews.review_comment_message` | 58,247 | 58.7% |
| `orders.order_delivered_customer_date` | 2,965 | 3.0% |
| `orders.order_delivered_carrier_date` | 1,783 | 1.8% |
| `orders.order_approved_at` | 160 | 0.2% |
| `products.product_category_name` (+ English name, name/description length, photos) | 610 | 1.9% |
| `products.product_weight_g` / `length_cm` / `height_cm` / `width_cm` | 2 | <0.1% |
| `geolocation.geolocation_lat` / `lng` | 5 | <0.1% |

**Known data-quality issues kept as-is** (documented in schema.yaml): 166 orders handed to the
carrier before purchase, 8 "delivered" orders with no delivery date, 9 payments of 0, 4 products
weighing 0 g, and messy free-text `seller_city` values.

## Design decisions

See [docs/decisions.md](docs/decisions.md).
