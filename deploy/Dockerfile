# Official multi-platform image digests verified against Docker Hub on 2026-09-17.
FROM docker.io/astral/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 AS uv
FROM docker.io/library/python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS dependencies

ARG SOURCE_DATE_EPOCH=0
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Wheels only: no unpinned build-system dependency resolution. The application
# runs directly from its reviewed source tree, with its bundled definitions.
RUN uv sync --locked --no-dev --no-install-project --no-build --no-cache \
    --python /usr/local/bin/python

FROM docker.io/library/python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS runtime
ARG SOURCE_DATE_EPOCH=0
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/aiab-cache \
    XDG_DATA_HOME=/tmp/aiab-data
WORKDIR /app
COPY --from=dependencies /app/.venv /app/.venv
COPY aiab/ /app/aiab/
COPY classifiers/ /app/classifiers/
USER 65532:65532
EXPOSE 8443 8080
CMD ["python", "-m", "aiab.main"]
