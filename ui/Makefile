# Run every target from this directory (ui/).
.PHONY: sync dev account admin dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

dev:
	uv run uvicorn server.app:app --reload --port $(PORT)

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
