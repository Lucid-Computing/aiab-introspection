# Run every target from this directory (ui/).
.PHONY: sync dev account admin dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

# With dev keys made, the server signs as the lab by itself (AIAB_LAB_KEY and
# AIAB_LAB_CERTIFICATE from $(KEYS)/lab.key and lab.cert) and as the auditor when an auditor
# presses Sign (AIAB_AUDITOR_KEY and AIAB_AUDITOR_CERTIFICATE from auditor.key and auditor.cert).
dev:
	$(if $(wildcard $(KEYS)/lab.key),AIAB_LAB_KEY=$$(cat $(KEYS)/lab.key) AIAB_LAB_CERTIFICATE=$$(cat $(KEYS)/lab.cert),) $(if $(wildcard $(KEYS)/auditor.key),AIAB_AUDITOR_KEY=$$(cat $(KEYS)/auditor.key) AIAB_AUDITOR_CERTIFICATE=$$(cat $(KEYS)/auditor.cert),) uv run uvicorn server.app:app --reload --port $(PORT)

# WorkOS login: admins are the lab, everyone else the auditor.   make admin EMAIL=teddy@lucid.example
# (REMOVE=1 takes them off; writes ui/admins.json, which is committed: commit and deploy)
admin:
	uv run python -m server.auth admin $(EMAIL) $(if $(REMOVE),--remove,)

# Local accounts (with or without WorkOS): make account NAME=teddy PARTY=lab
# (prompts for a password; writes ui/accounts.json)
account:
	uv run python -m server.auth add $(NAME) $(PARTY)

# Development party keys outside the repository, in the box's key format, plus a trust
# file that pins them. Start the server with AIAB_TRUST_FILE=$(KEYS)/trust.json.
dev-keys:
	uv run python -m server.devkeys $(KEYS)

lint:
	uv run ruff check .
	uv run ruff format --check server
