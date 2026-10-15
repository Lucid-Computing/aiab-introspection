# Run every target from this directory (ui/).
.PHONY: sync dev account dev-keys lint

KEYS ?= $(HOME)/.aiab-dev
PORT ?= 8090

sync:
	uv sync

dev:
	uv run uvicorn server.app:app --reload --port $(PORT)

# make account NAME=teddy PARTY=lab   (prompts for a password; writes ui/accounts.json)
account:
	uv run python -m server.auth add $(NAME) $(PARTY)

# Development party keys outside the repository, plus the trusted-parties file the UI reads.
dev-keys:
	uv run python -m aiab.plan_bundle keygen --party lab --out $(KEYS)/lab.key
	uv run python -m aiab.plan_bundle keygen --party auditor --out $(KEYS)/auditor.key
	uv run python -m aiab.plan_bundle trusted $(KEYS)/lab.key $(KEYS)/auditor.key --out $(KEYS)/trusted_parties.json
	@echo "set AIAB_TRUSTED_PARTIES=$(KEYS)/trusted_parties.json before 'make dev'"

lint:
	uv run ruff check .
	uv run ruff format --check server
