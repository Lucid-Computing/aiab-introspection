# Build from the repository root:  docker build -f ui/Dockerfile -t aiab-ui:local .
# Mount the checkout's plan folder at /data/plan so edits land in the repository.
FROM docker.io/astral/uv:0.11.6 AS uv
FROM docker.io/library/python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY static/pyproject.toml static/uv.lock static/README.md /app/static/
COPY static/aiab /app/static/aiab
COPY static/auditor /app/static/auditor
COPY ui/pyproject.toml ui/uv.lock /app/ui/
WORKDIR /app/ui
RUN uv sync --locked --no-dev
COPY ui /app/ui
ENV AIAB_PLAN_DIR=/data/plan UI_ACCOUNTS_FILE=/data/accounts.json
EXPOSE 8090
CMD ["uv", "run", "--no-sync", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8090"]
