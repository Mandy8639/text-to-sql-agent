# Text-to-SQL Analytics Agent

Ask a business question in plain English and get back SQL, a result table, a plain-English
explanation, and a chart. Built on the Olist Brazilian e-commerce dataset with PostgreSQL,
pgvector, and Claude.

> Status: **Phase 3 — evaluation benchmark and harness, baseline measured.** Later phases add
> retrieval and self-correction, safety, observability, the API/UI, and deployment.

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
uv run python -m app.cli "Which 5 states have the most unique customers?"
```

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

**Baseline results** (88 test questions, 74 answerable; full history in
[evals/results/summary.md](evals/results/summary.md)):

| Agent | Schema in prompt | Model | Exec acc | Hard | Abstain acc | p50 | $ / question |
|---|---|---|---:|---:|---:|---:|---:|
| baseline-v1 | full schema.yaml (6.2k tokens) | claude-opus-5-5 | **97.3%** | 100% | 0% | 3.9 s | $0.0325 |
| baseline-v1 | full schema.yaml | claude-haiku-5-5 | **94.6%** | 96.7% | 0% | 3.4 s | $0.0010 |
| baseline-raw-v1 | names, types, keys only (1.4k) | claude-haiku-5-5 | **79.7%** | 73.3% | 0% | 2.8 s | $0.0005 |

What this shows:

- **Documentation is worth about 15 points.** With schema.yaml's descriptions and business rules,
  even Haiku answers almost everything. Without them, it misses mostly questions whose answer
  depends on a company definition ("revenue" = payments on non-canceled orders), which no
  model can guess.
- **The documented setup leaves little headroom**: Opus solved all 30 hard questions. The
  remaining misses are about *when* the sales status filter applies.
- **Abstention is 0% everywhere.** The model often refuses in prose ("I can't drop tables") but
  still returns a substitute SELECT, because the baseline has no way to refuse.

See decisions D23–D25 in [docs/decisions.md](docs/decisions.md).

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
