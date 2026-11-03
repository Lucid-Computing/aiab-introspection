# Run every target from this directory (ui/).
.PHONY: sync dev account party dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

dev:
	uv run uvicorn server.app:app --reload --port $(PORT)

# WorkOS login: which party an email is.   make party EMAIL=teddy@lucid.example PARTY=lab
# (PARTY=none removes them; writes ui/parties.json, git-ignored)
party:
	uv run python -m server.auth party $(EMAIL) $(PARTY)

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
