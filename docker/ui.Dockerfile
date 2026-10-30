# UI image: Streamlit only. It talks to the API over HTTP (API_URL) and holds no secrets.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# Non-root user (uid 1000, which Hugging Face Spaces also expects), created first and owning
# /app: files are written with the right owner, so no recursive-chown layer duplicates them.
RUN useradd --create-home --uid 1000 appuser && mkdir /app && chown appuser /app
USER appuser
WORKDIR /app

COPY --chown=appuser pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --only-group ui --no-install-project

COPY --chown=appuser ui ./ui
COPY --chown=appuser .streamlit ./.streamlit

# In a container Streamlit must listen on all interfaces (config.toml says localhost).
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 API_URL=http://api:8000
EXPOSE 8501
CMD ["streamlit", "run", "ui/streamlit_app.py", "--server.port", "8501"]
