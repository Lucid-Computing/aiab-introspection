# Run every target from this directory (ui/).
.PHONY: sync dev account party dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

dev:
	uv run uvicorn server.app:app --reload --port $(PORT)

# Clerk login (CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY in ui/.env): bind a Clerk user
# to a party.   make party EMAIL=teddy@example.com PARTY=lab
party:
	uv run python -m server.auth set-party $(EMAIL) $(PARTY)

# Local accounts (no Clerk keys): make account NAME=teddy PARTY=lab
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
