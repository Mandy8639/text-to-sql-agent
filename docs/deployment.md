# Deployment

## Options (free tiers, checked 2026-10)

The app needs **Postgres 16 + pgvector** (153 MB: 144 MB data + a 240 KB index) and **one
container** with the API, the UI and the local embedding model (~1 GB RAM when running).

| Piece | Option | Free tier | Fit |
|---|---|---|---|
| Database | **Neon** (chosen) | 0.5 GB; suspends after 5 min idle (about 1 s to wake) | ✅ fits; pgvector + unaccent; normal SQL roles, so the least-privilege roles carry over |
| Database | Supabase | 500 MB; project pauses after a week without activity | ✅ fits; also pgvector |
| App | **Hugging Face Spaces, Docker** (chosen) | 2 vCPU, **16 GB RAM**; sleeps after 48 h idle (about 1 min cold start) | ✅ plenty of RAM for the embedding model; secrets UI; public URL |
| App | Render free web service | 512 MB RAM; spins down after 15 min (about 1 min cold start) | ⚠️ tight for API + model + Streamlit |
| App | Streamlit Community Cloud | UI only; needs a public GitHub repo | ❌ the API would still need a host |
| App | Fly.io, Railway | no longer free (trial credit only) | ❌ |

**Chosen: Neon + Hugging Face Spaces.** Costs $0/month. The tradeoff is cold starts after idle
periods, which is fine for a portfolio demo.

## Architecture as deployed

```
visitor ──HTTPS──> HF Space container (port 7860)
                     ├─ Streamlit UI   0.0.0.0:7860 (public)
                     └─ FastAPI        127.0.0.1:8000 (container-internal only)
                           ├─ olist_agent  ──> Neon: olist.*        (SELECT only)
                           ├─ olist_rag    ──> Neon: rag.*          (SELECT only)
                           ├─ olist_app    ──> Neon: app.spend_daily (write, that table only)
                           ├─ Anthropic API (Claude Haiku 5.5)
                           └─ Langfuse Cloud (traces, feedback)
```

**The deployed app never receives the admin `DATABASE_URL`.** Data is loaded once, from a
laptop, with the Neon owner role. The container gets only the three least-privilege URLs.

## Cost protection

| Layer | Default | Where |
|---|---|---|
| Model | Claude Haiku 5.5 (~$0.001/question incl. explanation) | `LLM_MODEL` |
| Per-visitor limit | 10 questions/min (set 5 on the demo) | `API_RATE_LIMIT_PER_MINUTE` |
| Global limit | 30 questions/min across all visitors | `API_GLOBAL_RATE_LIMIT_PER_MINUTE` |
| Daily spend cap | $1.00/day (UTC), stored in Postgres so restarts can't reset it | `DAILY_BUDGET_USD` |
| Anthropic console limit | set a monthly limit (e.g. $10) as the last backstop | console.anthropic.com |

The UI forwards each visitor's IP (from the platform's proxy header) to the API, which trusts it
only from inside the container. That keeps the per-visitor limit per visitor even though every
request reaches the API from the UI.

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

### 2. Hugging Face Space (app)

1. Sign up at huggingface.co → **New Space** → SDK **Docker** → *Blank* → hardware
   **CPU basic (free)** → visibility *Public* (for a demo link).
2. Space **Settings → Variables and secrets**. Add as **secrets**:
   `ANTHROPIC_API_KEY`, `AGENT_DATABASE_URL`, `RAG_DATABASE_URL`, `APP_DATABASE_URL`,
   `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. Add as **variables**:
   `LLM_MODEL=claude-haiku-5-5`, `DAILY_BUDGET_USD=1`, `API_RATE_LIMIT_PER_MINUTE=5`,
   `API_GLOBAL_RATE_LIMIT_PER_MINUTE=20`.
3. Create an HF **access token** with write access (Settings → Access Tokens), then push:

   ```bash
   HF_SPACE=<your-username>/<space-name> scripts/deploy_space.sh
   ```

   The script copies only runtime code (no `.env`, data, tests or eval results) into the
   Space repo. The first build takes about 5 min (it bakes the embedding model into the image).
4. Open `https://huggingface.co/spaces/<user>/<space-name>`. The sidebar shows "API online"
   once the database and index are reachable.

### Updating

Re-run `scripts/deploy_space.sh` after changes. If schema.yaml or the dev examples change,
also re-run `make build-index` against Neon (with `.env.neon` loaded).

## Local equivalents

| Command | What |
|---|---|
| `make stack-up` / `make stack-down` | db + api + ui in Docker (http://127.0.0.1:8501) |
| `make space-image` | build the exact Space image locally |
| `docker run --rm -p 127.0.0.1:7860:7860 --env-file .env -e DB_HOST_OVERRIDE=host.docker.internal text2sql-space` | run the Space image against the local DB |
