# Text-to-SQL Analytics Agent

Ask a business question in plain English and get back SQL, a result table, a plain-English
explanation, and a chart. Built on the Olist Brazilian e-commerce dataset with PostgreSQL,
pgvector, and Claude.

> Status: **Phase 1 — data loaded and documented.** Later phases add the agent, the eval
> benchmark, safety, observability, the API/UI, and deployment.

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (Docker Desktop, OrbStack, or Colima),
`make`, and the [Olist dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(9 CSVs, unzipped into `data/raw/`).

```bash
make setup      # install deps, create .env from .env.example
# edit .env: set ANTHROPIC_API_KEY; change the passwords in POSTGRES_PASSWORD,
# DATABASE_URL and AGENT_DATABASE_URL
make db-up      # start Postgres 16 + pgvector and wait until healthy
make load-data  # load the CSVs (~10 s) and create the read-only agent role
make test       # run tests
```

## Commands

| Command          | What it does                                               |
| ---------------- | ---------------------------------------------------------- |
| `make setup`     | `uv sync` and create `.env` if missing                     |
| `make db-up`     | Start Postgres + pgvector (waits for the healthcheck)      |
| `make db-down`   | Stop Postgres (keeps data)                                 |
| `make db-reset`  | Stop Postgres and delete its data volume                   |
| `make load-data` | (Re)load CSVs into the `olist` schema; create agent role   |
| `make profile-data` | Print the data profile below as Markdown                |
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
| `DATABASE_URL`      | local admin URL    | Admin connection for scripts only           |
| `AGENT_DATABASE_URL`| local agent URL    | Read-only role the agent must use           |
| `ANTHROPIC_API_KEY` | —                  | Claude API key                              |
| `LLM_MODEL`         | `claude-opus-5-5`  | Model ID                                    |
| `LLM_EFFORT`        | `medium`           | `low` / `medium` / `high` / `xhigh` / `max` |
| `LLM_MAX_TOKENS`    | `16000`            | Output token cap per call                   |

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
