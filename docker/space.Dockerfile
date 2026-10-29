# Hugging Face Spaces (Docker SDK): API + UI in ONE container, free CPU tier (16 GB RAM).
# Only the UI port (7860) is public; the API listens on 127.0.0.1:8000 inside the container.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# Non-root user (uid 1000, which Hugging Face Spaces also expects), created first and owning
# /app: files are written with the right owner, so no recursive-chown layer duplicates them.
RUN useradd --create-home --uid 1000 appuser && mkdir /app && chown appuser /app
USER appuser
WORKDIR /app

COPY --chown=appuser pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-default-groups --group ui --no-install-project

ENV EMBEDDING_CACHE_DIR=/app/.cache/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.cache/fastembed')"

COPY --chown=appuser app ./app
COPY --chown=appuser api ./api
COPY --chown=appuser ui ./ui
COPY --chown=appuser .streamlit ./.streamlit
COPY --chown=appuser docs/schema.yaml ./docs/schema.yaml
COPY --chown=appuser docker/space_start.sh ./space_start.sh

ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 API_URL=http://127.0.0.1:8000
EXPOSE 7860
CMD ["./space_start.sh"]
