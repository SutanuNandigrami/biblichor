.PHONY: install test lint fmt run bench

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check . && ruff format --check .

fmt:
	ruff format . && ruff check . --fix

run:
	python -m endless_library run

bench:
	python -m endless_library bench --quick
