#!/bin/sh
# Build-only toolchain; credentials and runtime data never enter this stage.
set -eu
curl --fail --silent --show-error --location \
  https://go.dev/dl/go1.27.3.linux-amd64.tar.gz -o /tmp/go.tar.gz
printf '%s  %s\n' \
  65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40 \
  /tmp/go.tar.gz | sha256sum --check --status
tar -C /usr/local -xzf /tmp/go.tar.gz
rm /tmp/go.tar.gz
make -C /build/static/agent_runtime/verified_transport \
  verify test all GO=/usr/local/go/bin/go
