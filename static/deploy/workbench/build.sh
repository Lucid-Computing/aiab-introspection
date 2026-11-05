#!/bin/sh
set -eu
# Pin the complete base images; the resulting IMAGE digest is separately approved
# and used by the measured confidential release. This script does not deploy it.
: "${NODE_IMAGE:?set digest-pinned node:22.22.2-bookworm-slim image}"
: "${PYTHON_IMAGE:?set digest-pinned Python 3.12 bookworm image}"
: "${UV_IMAGE:?set digest-pinned uv 0.11.6 image}"
for image in "$NODE_IMAGE" "$PYTHON_IMAGE" "$UV_IMAGE"; do
  printf '%s\n' "$image" | rg -q '@sha256:[a-f0-9]{64}$' || {
    echo 'Each base image must be pinned by sha256 digest' >&2; exit 1;
  }
done
test -s static/deploy/workbench/authority.pub || {
  echo 'Install the independently approved PUBLIC authority root before building' >&2; exit 1;
}
docker build --platform linux/amd64 -f static/deploy/workbench/Dockerfile \
  --build-arg NODE_IMAGE="$NODE_IMAGE" --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" \
  --build-arg UV_IMAGE="$UV_IMAGE" -t "${IMAGE_TAG:-aiab-confidential:review}" .
