# syntax=docker/dockerfile:1
# The agreement UI as one container. Build from the repository root so static/ is in the
# build context:
#     docker build -f ui/Dockerfile -t aiab-ui .
# The context is filtered by ui/Dockerfile.dockerignore (BuildKit reads the ignore file that
# sits next to the Dockerfile), not by the root .dockerignore, which is the enclave image's.
#
# At run time the container keeps a git checkout of the repository on a persistent disk
# (/data) and edits, signs and pushes from there; see docker-entrypoint.sh.
FROM docker.io/astral/uv:0.11.6 AS uv

FROM docker.io/library/python:3.12-slim
# git pushes the deploy branches, gh opens the pull requests.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# The static part the UI imports (digest, loader, signatures, auditor CLI) and the release pins.
COPY static/pyproject.toml static/uv.lock static/README.md /app/static/
COPY static/aiab /app/static/aiab
COPY static/auditor /app/static/auditor
COPY static/deploy/tinfoil/audit/release.json /app/static/deploy/tinfoil/audit/release.json
COPY ui/pyproject.toml ui/uv.lock /app/ui/
WORKDIR /app/ui
RUN uv sync --locked --no-dev
COPY ui /app/ui
RUN chmod +x /app/ui/docker-entrypoint.sh
EXPOSE 8090
ENTRYPOINT ["/app/ui/docker-entrypoint.sh"]
