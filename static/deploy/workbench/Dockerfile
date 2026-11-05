# syntax=docker/dockerfile:1
# Required digest-pinned image arguments are supplied by build.sh. Build at repo root.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS auditor
RUN test "$(node --version)" = v22.22.2
RUN apt-get update && apt-get install -y --no-install-recommends make gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build/static/agent_runtime
COPY static/agent_runtime/package.json static/agent_runtime/package-lock.json ./
RUN npm ci --ignore-scripts
COPY static/agent_runtime ./
RUN make -B NODE_INCLUDE=/usr/local/include/node
FROM ${PYTHON_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home runtime \
    && mkdir /private && chown runtime:runtime /private
COPY --from=uv /uv /usr/local/bin/uv
RUN test "$(uv --version)" = "uv 0.11.6" && python -c "import sys; assert sys.version_info[:2] == (3, 12)"
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/static
COPY static/pyproject.toml static/uv.lock static/README.md ./
COPY static/aiab ./aiab
COPY static/auditor ./auditor
RUN uv sync --locked --no-dev
COPY --from=auditor /usr/local/bin/node /usr/local/bin/node
COPY --from=auditor /build/static/agent_runtime /app/static/agent_runtime
# This public root must be independently reviewed, NEVER a private signing key.
COPY static/deploy/workbench/authority.pub /app/deploy/authority.pub
COPY static/deploy/workbench/entrypoint.sh /app/deploy/entrypoint.sh
USER runtime
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD ["/app/static/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3)"]
ENTRYPOINT ["/bin/sh", "/app/deploy/entrypoint.sh"]
