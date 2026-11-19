# Run every target from this directory (ui/).
.PHONY: sync dev account admin dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

# With dev keys made, the server certifies each member's browser key on their first Sign
# (AIAB_LAB_ROOT_KEY and AIAB_AUDITOR_ROOT_KEY from $(KEYS)/lab-root.key and auditor-root.key), and the
# lab card offers the server's Sign to the auditor (AIAB_LAB_KEY from lab.key).
dev:
	$(if $(wildcard $(KEYS)/lab.key),AIAB_LAB_KEY=$$(cat $(KEYS)/lab.key) AIAB_LAB_CERTIFICATE=$$(cat $(KEYS)/lab.cert),) $(if $(wildcard $(KEYS)/lab-root.key),AIAB_LAB_ROOT_KEY=$$(cat $(KEYS)/lab-root.key),) $(if $(wildcard $(KEYS)/auditor-root.key),AIAB_AUDITOR_ROOT_KEY=$$(cat $(KEYS)/auditor-root.key),) uv run uvicorn server.app:app --reload --port $(PORT)

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
