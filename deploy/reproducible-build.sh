#!/usr/bin/env bash
# Compare two clean OCI exports; keep evidence, remove only this temporary builder.
set -euo pipefail

command -v docker >/dev/null
command -v jq >/dev/null
command -v python3 >/dev/null
aiab_repro_buildx=${AIAB_BUILDX_BIN:-}
if [[ -z "$aiab_repro_buildx" ]]; then
  aiab_repro_buildx=$(docker info --format '{{range .ClientInfo.Plugins}}{{if eq .Name "buildx"}}{{.Path}}{{end}}{{end}}')
fi
if [[ ! -f "$aiab_repro_buildx" || ! -x "$aiab_repro_buildx" ]]; then
  printf '%s\n' 'Buildx is required; set AIAB_BUILDX_BIN to a reviewed executable.' >&2
  exit 1
fi

aiab_repro_dir=$(mktemp -d "${TMPDIR:-/tmp}/aiab-repro.XXXXXX")
mkdir -p "$aiab_repro_dir/docker-config/cli-plugins"
cp "$aiab_repro_buildx" "$aiab_repro_dir/docker-config/cli-plugins/docker-buildx"
export DOCKER_CONFIG="$aiab_repro_dir/docker-config"
aiab_repro_builder="aiab-repro-${aiab_repro_dir##*.}"
aiab_repro_builder=${aiab_repro_builder,,}
aiab_repro_buildkit='moby/buildkit:v0.26.2@sha256:de10faf919fc71ba4eb1dd7bd6449566d012b0c9436b1c61bfee21d621b009aa'
printf 'Reproducibility evidence directory: %s\n' "$aiab_repro_dir"
docker buildx version
trap 'docker buildx rm "$aiab_repro_builder" >/dev/null 2>&1 || true' EXIT
docker buildx create --name "$aiab_repro_builder" --driver docker-container \
  --driver-opt "image=$aiab_repro_buildkit" --bootstrap
docker buildx inspect "$aiab_repro_builder"

for aiab_repro_run in 1 2; do
  docker buildx build --builder "$aiab_repro_builder" --no-cache \
    --platform linux/amd64 --provenance=false --sbom=false \
    --build-arg SOURCE_DATE_EPOCH=0 -f deploy/Dockerfile \
    --output "type=oci,dest=$aiab_repro_dir/build-$aiab_repro_run.tar,rewrite-timestamp=true,compression=gzip,compression-level=6,force-compression=true" \
    --metadata-file "$aiab_repro_dir/build-$aiab_repro_run.json" .
  python3 deploy/inspect_oci.py "$aiab_repro_dir/build-$aiab_repro_run.tar"
done

aiab_repro_first=$(jq -er '."containerimage.digest" | select(test("^sha256:[0-9a-f]{64}$"))' "$aiab_repro_dir/build-1.json")
aiab_repro_second=$(jq -er '."containerimage.digest" | select(test("^sha256:[0-9a-f]{64}$"))' "$aiab_repro_dir/build-2.json")
printf 'First OCI image digest:  %s\nSecond OCI image digest: %s\n' "$aiab_repro_first" "$aiab_repro_second"
if [[ "$aiab_repro_first" != "$aiab_repro_second" ]]; then
  printf '%s\n' 'FAIL: clean OCI image digests differ; inspect retained metadata and layer blobs.' >&2
  exit 1
fi
printf '%s\n' 'PASS: clean OCI image digests match (config and layers; not tar file timestamps).'
