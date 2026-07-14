PYTHON := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: bootstrap dev start test test-api test-web test-e2e build generate-types

bootstrap:
	python3.13 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.lock
	$(PIP) install -e apps/api --no-deps
	npm --prefix apps/web ci
	npm --prefix apps/web exec playwright install chromium

dev:
	./scripts/dev.sh

build:
	npm --prefix apps/web run build

generate-types:
	$(PYTHON) scripts/export_openapi.py
	npm --prefix apps/web run generate:api

start: build
	./scripts/start.sh

test: test-api test-web test-e2e build

test-api:
	$(PYTHON) -m pytest apps/api/tests

test-web:
	npm --prefix apps/web run test -- --run
	npm --prefix apps/web run typecheck

test-e2e:
	npm --prefix apps/web run test:e2e
