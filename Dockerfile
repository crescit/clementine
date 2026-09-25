FROM python:3.12-slim

WORKDIR /app

# Non-root user with writable /data for SQLite
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY clearpath ./clearpath
COPY static ./static

RUN uv sync --frozen --no-dev --no-editable \
    && chown -R app:app /app

ENV DATABASE_PATH=/data/clearpath.db \
    DEMO_MODE=true \
    PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

# Shell form so ${PORT:-8000} expands on hosts that inject PORT
CMD ["sh", "-c", "exec uvicorn clearpath.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
