# ---- builder: resolve and install locked deps into a self-contained venv ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock ./
# No BuildKit cache mount: Cloud Build's default builder is the legacy one, and
# the uv cache only lives in this discarded builder stage anyway.
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# tiktoken downloads its BPE file on first use; bake it in so cold starts don't
# depend on internet egress (app/services/context_window.py loads it at import).
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# ---- runtime: slim image, no uv / build tooling, non-root ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PORT=8080

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/tiktoken /opt/tiktoken
# Code stays root-owned (read-only to the app user). Only what runtime needs:
# the API/worker package plus alembic for a migration job using this image.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Precompile app bytecode at build time (the app user can't write .pyc at runtime).
RUN python -m compileall -q app alembic

USER app
EXPOSE 8080

# No ENTRYPOINT on purpose: Cloud Run's --command replaces ENTRYPOINT and --args
# replaces CMD, so the same image runs Celery or alembic, e.g.
#   --command celery --args "-A,app.celery_app,worker,--beat,--schedule=/tmp/celerybeat-schedule,--loglevel=INFO"
#   --command alembic --args "upgrade,head"
# sh -c expands $PORT (set by Cloud Run); exec makes uvicorn PID 1 so it gets SIGTERM.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
