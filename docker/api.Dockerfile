# API image: FastAPI + agent + retrieval. No Streamlit (ui group), no dev tools.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# Non-root user (uid 1000, which Hugging Face Spaces also expects), created first and owning
# /app: files are written with the right owner, so no recursive-chown layer duplicates them.
RUN useradd --create-home --uid 1000 appuser && mkdir /app && chown appuser /app
USER appuser
WORKDIR /app

# Dependencies first (cached layer): only the main dependency set from the lockfile.
COPY --chown=appuser pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-default-groups --no-install-project

# Bake the embedding model into the image so the first request doesn't download 130 MB.
ENV EMBEDDING_CACHE_DIR=/app/.cache/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.cache/fastembed')"

COPY --chown=appuser app ./app
COPY --chown=appuser api ./api
COPY --chown=appuser docs/schema.yaml ./docs/schema.yaml

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
