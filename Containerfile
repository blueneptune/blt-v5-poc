# The API image. Only the "api" extra is installed - the collector (and
# its git dependency on sdk-primer-core) runs on the host via collect.sh.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so editing source doesn't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra api --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --extra api

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Listens on 0.0.0.0:8000 unless told otherwise; deploy/up.sh overrides
# both when the pod uses the host's network (BLT_POD_NETWORK=host).
CMD ["sh", "-c", "blt-migrate && exec uvicorn --factory blt.api.app:create_app --host ${BLT_LISTEN_HOST:-0.0.0.0} --port ${BLT_LISTEN_PORT:-8000}"]
