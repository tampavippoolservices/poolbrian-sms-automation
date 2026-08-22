PYTHON := .venv/bin/python

.PHONY: install format lint typecheck test audit build check

install:
	.venv/bin/pip install --require-hashes -r requirements-dev.lock
	.venv/bin/pip install --no-deps --no-build-isolation -e .

format:
	.venv/bin/ruff format app migrations tests
	.venv/bin/ruff check --fix app migrations tests

lint:
	.venv/bin/ruff check app migrations tests
	.venv/bin/ruff format --check app migrations tests

typecheck:
	.venv/bin/mypy app

test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing --cov-fail-under=40

audit:
	$(PYTHON) -m pip_audit --strict --no-deps --disable-pip \
		--cache-dir /tmp/tampa-vip-pip-audit -r requirements.lock

build:
	$(PYTHON) -m build --no-isolation

check: lint typecheck test build
