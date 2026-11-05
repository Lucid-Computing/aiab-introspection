#!/bin/sh
set -eu
umask 077
ulimit -c 0
# Fixed single worker. No reload/access log/trace stream or environment worker count.
exec /app/static/.venv/bin/uvicorn aiab.workbench.confidential_app:factory \
  --factory --host 0.0.0.0 --port 8080 --workers 1 --no-access-log --log-level warning
