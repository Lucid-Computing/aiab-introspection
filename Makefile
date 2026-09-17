.PHONY: sync check lint format test local-up local-down traffic eval image demo-up demo-down

PYTHON_DIRS := aiab auditor traffic tests
COMPOSE ?= docker compose
LOCAL_COMPOSE := $(COMPOSE) --env-file /dev/null -p aiab-local-monitor -f deploy/docker-compose.local.yml

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
	docker build -f deploy/Dockerfile -t aiab:local .

demo-up:
	docker compose -f deploy/docker-compose.demo.yml up --build -d

demo-down:
	docker compose -f deploy/docker-compose.demo.yml down
