#!/bin/sh
# Start the agreement UI in a container.
#
# The UI code runs from the image. The repository checkout the UI edits lives on the
# persistent disk (AIAB_DATA, default /data) and is cloned on first boot; so do the
# accounts, the trust file and the runs. Secrets arrive as environment variables and are
# written to the files the tools expect, never baked into the image.
set -eu
DATA="${AIAB_DATA:-/data}"
REPO="${AIAB_REPO:?set AIAB_REPO to owner/name}"
URL="${AIAB_REPO_URL:-https://github.com/$REPO.git}"
BRANCH="${AIAB_CHECKOUT_BRANCH:-master}"
mkdir -p "$DATA/runs" "$DATA/demo-keys"
umask 077

# Identity for the deploy commits; the GitHub token serves both git push and gh.
git config --global user.name "${GIT_AUTHOR_NAME:-AIAB agreement UI}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-aiab-agreement-ui@users.noreply.github.com}"
git config --global --add safe.directory '*'
if [ -n "${GITHUB_TOKEN:-}" ]; then
  export GH_TOKEN="$GITHUB_TOKEN"
  gh auth setup-git || echo "warning: gh could not configure git credentials"
fi

if [ ! -d "$DATA/repo/.git" ]; then
  echo "first boot: cloning $URL ($BRANCH) into $DATA/repo"
  git clone --quiet --branch "$BRANCH" "$URL" "$DATA/repo"
fi
mkdir -p "$DATA/repo/agreements"
# Follow the configured branch: switch if AIAB_CHECKOUT_BRANCH changed since the clone, then
# pick up what landed on it since the last start (merges from master, new release pins).
# Only when nothing unsaved sits in the working tree.
git -C "$DATA/repo" remote set-url origin "$URL"  # the repository may have moved since the clone
if [ -z "$(git -C "$DATA/repo" status --porcelain)" ]; then
  if [ "$(git -C "$DATA/repo" rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
    git -C "$DATA/repo" fetch --quiet origin "$BRANCH" \
      && git -C "$DATA/repo" switch --quiet --track -C "$BRANCH" "origin/$BRANCH" \
      && echo "switched the repository copy to $BRANCH"
  fi
  git -C "$DATA/repo" pull --ff-only --quiet origin "$BRANCH" || echo "warning: could not fast-forward $BRANCH"
else
  echo "note: unsaved changes in $DATA/repo, not pulling or switching"
fi
if [ ! -f "$DATA/trust.json" ]; then
  cp /app/static/aiab/baked/trust.json "$DATA/trust.json"
fi
if [ -n "${LAB_TOKEN:-}" ]; then printf '%s\n' "$LAB_TOKEN" > "$DATA/demo-keys/lab.token"; fi
if [ -n "${LOGGER_KEY:-}" ]; then printf '%s\n' "$LOGGER_KEY" > "$DATA/demo-keys/logger.key"; fi

export AIAB_GIT_DIR="$DATA/repo" AIAB_PLAN_DIR="$DATA/repo/plan" AIAB_AGREEMENTS_DIR="$DATA/repo/agreements"
export AIAB_TRUST_FILE="$DATA/trust.json" UI_ACCOUNTS_FILE="$DATA/accounts.json"
export AIAB_RUNS_DIR="$DATA/runs" AIAB_DEMO_KEYS="$DATA/demo-keys" AIAB_LOGS_DIR="$DATA/logs"
# Runs use an independent production snapshot. Unsaved drafts must neither block release
# updates nor remove the signatures needed to run the already-deployed plan.
DEPLOYED=$(uv run --no-sync python -m server.deployed)
export AIAB_RUN_PLAN_DIR="$DEPLOYED/plan"
export AIAB_RELEASE_FILE="${AIAB_RELEASE_FILE:-$DEPLOYED/static/deploy/tinfoil/audit/release.json}"

# First boot only: accounts from UI_BOOTSTRAP_ACCOUNTS="user:party:password,...".
if [ ! -f "$DATA/accounts.json" ] && [ -n "${UI_BOOTSTRAP_ACCOUNTS:-}" ]; then
  uv run --no-sync python -m server.auth bootstrap
fi
exec uv run --no-sync uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8090}"
