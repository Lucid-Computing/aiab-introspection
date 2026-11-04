# Run every target from this directory (static/). The image build uses the repository root as context.
.PHONY: sync check lint format test image image-reproducible

PYTHON_DIRS := aiab auditor tests deploy

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

image:
	docker build -f deploy/Dockerfile -t aiab:local ..

image-reproducible:
	bash deploy/reproducible-build.sh
