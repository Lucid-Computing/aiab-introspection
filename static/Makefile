# Run every target from this directory (static/). Image builds use the repository root as context.
.PHONY: sync check lint format test local-up local-down traffic eval image image-reproducible demo-up demo-down hosted-up hosted-down hosted-config

PYTHON_DIRS := aiab auditor traffic tests deploy
COMPOSE ?= docker compose
LOCAL_COMPOSE := $(COMPOSE) --env-file /dev/null -p aiab-local-monitor -f deploy/docker-compose.local.yml
HOSTED_COMPOSE := $(COMPOSE) --env-file /dev/null -f deploy/docker-compose.hosted.yml

sync:
	uv sync --locked

check: lint test

lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check $(PYTHON_DIRS)

format:
	uv run --locked ruff format $(PYTHON_DIRS)

test:
	uv run --locked pytest

local-up:
	$(LOCAL_COMPOSE) up --build -d --wait --wait-timeout 300

local-down:
	$(LOCAL_COMPOSE) down

traffic:
	uv run --locked python -m traffic.generate --url https://localhost:8443 --count 200 --insecure

eval:
	uv run --locked python -m traffic.evaluate

image:
	docker build -f deploy/Dockerfile -t aiab:local ..

image-reproducible:
	bash deploy/reproducible-build.sh

demo-up:
	$(COMPOSE) --env-file /dev/null -f deploy/docker-compose.demo.yml up --build -d --wait --wait-timeout 300

demo-down:
	$(COMPOSE) --env-file /dev/null -f deploy/docker-compose.demo.yml down

hosted-config:
	$(HOSTED_COMPOSE) config --quiet

hosted-up:
	$(HOSTED_COMPOSE) up --build -d --wait --wait-timeout 300

hosted-down:
	$(HOSTED_COMPOSE) down
