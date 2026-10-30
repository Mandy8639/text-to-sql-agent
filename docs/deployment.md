# Deployment

## Options (checked on the providers' own pages, 2026-10-30)

The app needs **Postgres + pgvector** (153 MB: 144 MB data + a 240 KB index) and one Python
process with the agent, UI and local embedding model. **Measured:** about 270 MB for the API alone
and **about 400 MB** for the UI with the agent in-process.

| Piece | Option | What the provider says | Fit |
|---|---|---|---|
| Database | **Neon** (chosen) | free plan used for this project; Postgres 18 | ✅ fits; pgvector + unaccent; normal SQL roles, so the least-privilege roles carry over |
| App | **Streamlit Community Cloud** (chosen) | 690 MB–2.7 GB memory (figures dated Feb 2024); sleeps after 12 h without visitors; reads `uv.lock`, `requirements.txt` and others | ✅ fits; one app, no separate API host |
| App | Render free web service | 750 hours/month; spins down after 15 min, about 1 min to wake; "do not use for production"; memory not stated on that page | ⚠️ workable, but needs a separate host for the UI |
| App | Hugging Face Spaces | **Docker and Gradio Spaces require a paid plan** (only Static is free) | ❌ not free; works as built if you pay |
| App | Koyeb | pricing page shows no free web service | ❌ |

**Correction:** an earlier version of this page said Docker Spaces were free. That came from memory, not from
a check, and was wrong. The limits above were read from each provider's page; they change, so
re-check before relying on them.

**Chosen: Neon + Streamlit Community Cloud.** The tradeoff is sleeping after idle periods.

## Architecture as deployed

```
visitor --HTTPS--> Streamlit Community Cloud (UI_MODE=inprocess)
                     Streamlit UI + QueryService + local embedding model, in one process
                       |- olist_agent --> Neon: olist.*          (SELECT only)
                       |- olist_rag   --> Neon: rag.*            (SELECT only)
                       |- olist_app   --> Neon: app.spend_daily  (write, that table only)
                       |- Anthropic API (Claude Haiku 5.5)
                       '- Langfuse Cloud (traces, feedback)
```

There is no public API on this host: the FastAPI service remains in the repo for local and Docker use.
`app/limits.py` holds the validation, rate limits and spend cap, so both paths enforce the same rules.

**The deployed app never receives the admin `DATABASE_URL`.** Data is loaded once, from a
laptop, with the Neon owner role. The app gets only the three least-privilege URLs.

## Cost protection

| Layer | Default | Where |
|---|---|---|
| Model | Claude Haiku 5.5 (~$0.001/question incl. explanation) | `LLM_MODEL` |
| Per-visitor limit | 10 questions/min (set 5 on the demo) | `API_RATE_LIMIT_PER_MINUTE` |
| Global limit | 30 questions/min across all visitors | `API_GLOBAL_RATE_LIMIT_PER_MINUTE` |
| Daily spend cap | $1.00/day (UTC), stored in Postgres so restarts can't reset it | `DAILY_BUDGET_USD` |
| Anthropic console limit | set a monthly limit (e.g. $10) as the last backstop | console.anthropic.com |

Visitors are identified by IP when the host exposes it, otherwise by session. A session id can
be reset by reloading, so the global limit and the daily cap are the real protection.

## Step by step

### 1. Neon (database)

1. Sign up at neon.tech, create a project (region near you, Postgres 16), and copy the
   **connection string** of the owner role (`neondb_owner`), e.g.
   `postgresql://neondb_owner:...@ep-xxx.<region>.aws.neon.tech/neondb?sslmode=require`.
2. In the SQL editor run `CREATE EXTENSION IF NOT EXISTS vector;`.
3. From your laptop, load everything into Neon with a **separate env file** (never commit it):

   ```bash
   cp .env .env.neon   # then edit .env.neon:
   #   DATABASE_URL       = postgresql+psycopg://neondb_owner:<pw>@<host>/neondb?sslmode=require
   #   AGENT_DATABASE_URL = postgresql+psycopg://olist_agent:<new random pw>@<host>/neondb?sslmode=require
   #   RAG_DATABASE_URL   = postgresql+psycopg://olist_rag:<new random pw>@<host>/neondb?sslmode=require
   #   APP_DATABASE_URL   = postgresql+psycopg://olist_app:<new random pw>@<host>/neondb?sslmode=require
   set -a && . ./.env.neon && set +a && make bootstrap
   ```

   `make bootstrap` = load the CSVs (one transaction), create the read-only agent role, build
   the pgvector index and its role, and create the spend table and its role. About 1–2 min over
   the network.

### 2. Streamlit Community Cloud (app)

1. Sign in at share.streamlit.io with GitHub and allow access to the repository.
2. **Create app** → deploy from GitHub: repository `Mandy8639/text-to-sql-agent`, branch `main`,
   main file `ui/streamlit_app.py`.
3. **Advanced settings:** Python **3.12**, and paste the settings into **Secrets** (TOML):

   ```toml
   UI_MODE = "inprocess"
   LLM_MODEL = "claude-haiku-5-5"
   DAILY_BUDGET_USD = "1"
   API_RATE_LIMIT_PER_MINUTE = "5"
   API_GLOBAL_RATE_LIMIT_PER_MINUTE = "20"
   AGENT_DATABASE_URL = "..."
   RAG_DATABASE_URL = "..."
   APP_DATABASE_URL = "..."
   ANTHROPIC_API_KEY = "..."
   LANGFUSE_PUBLIC_KEY = "..."
   LANGFUSE_SECRET_KEY = "..."
   ```

   Never add `DATABASE_URL` here. `ui/requirements.txt` sits next to the entrypoint, so it is
   found first; CI fails if it drifts from `uv.lock` (`make requirements` regenerates it).
4. Deploy. The first start installs dependencies and downloads the embedding model. The sidebar
   shows "Agent online" once the database and index are reachable.

### Updating

Pushing to `main` redeploys the app. If schema.yaml or the dev examples change, also re-run
`make build-index` against Neon (with `.env.neon` loaded).

### Alternative: one container on a paid host

`docker/space.Dockerfile` runs API + UI in one container (`make space-image`), and
`scripts/deploy_space.sh` pushes it to a Hugging Face Docker Space, which needs a paid plan.

## Local equivalents

| Command | What |
|---|---|
| `make stack-up` / `make stack-down` | db + api + ui in Docker (http://127.0.0.1:8501) |
| `make space-image` | build the exact Space image locally |
| `docker run --rm -p 127.0.0.1:7860:7860 --env-file .env -e DB_HOST_OVERRIDE=host.docker.internal text2sql-space` | run the Space image against the local DB |
