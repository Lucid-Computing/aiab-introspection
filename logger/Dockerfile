# syntax=docker/dockerfile:1
# The logger's side of AIAB as one container, to run next to the logging proxy. Build from
# the repository root so static/ is in the build context:
#     docker build -f logger/Dockerfile -t aiab-logger .
# The context is filtered by logger/Dockerfile.dockerignore (BuildKit reads the ignore file
# that sits next to the Dockerfile).
#
# Nothing secret is in the image. The keys and the addresses come from the environment at
# run time; see logger/README.md. The proxy's data volume is mounted read-only at
# /proxy-data, and the logger writes nothing there.
#
# The one thing written at run time is the box's attestation check: sigstore keeps its TUF
# trust root and the Tinfoil SDK its bundles under the XDG cache and data directories,
# pointed at /tmp below. With --read-only, give it a tmpfs there (--tmpfs /tmp): without
# one, /v1/export fails with a 502 ("Read-only file system").
FROM docker.io/astral/uv:0.11.6 AS uv

FROM docker.io/library/python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# The box's sealing code, so a record is sealed here exactly as the box opens it.
COPY static/pyproject.toml static/uv.lock static/README.md /app/static/
COPY static/aiab /app/static/aiab
COPY logger/pyproject.toml logger/uv.lock /app/logger/
WORKDIR /app/logger
RUN uv sync --locked --no-dev
COPY logger/logserver /app/logger/logserver
RUN useradd --uid 10001 --create-home logger
USER logger
ENV PATH="/app/logger/.venv/bin:$PATH" XDG_CACHE_HOME=/tmp/cache XDG_DATA_HOME=/tmp/data
EXPOSE 8080
CMD ["uvicorn", "--factory", "logserver.app:main", "--host", "0.0.0.0", "--port", "8080"]
