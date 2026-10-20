# shiny-mcp-host developer commands. Requires: uv, Node 20+, npm.
PY        := .venv/bin/python
PYTEST    := $(PY) -m pytest
DEMO_PORT ?= 3001
APP_PORT  ?= 8000

.PHONY: setup setup-compat build lint test-unit test-integration test-e2e test-security \
        test-compat test-dist test demo demo-standalone clean

setup:                  ## reproducible Python + Node install (from uv.lock / package-lock.json)
	uv sync --frozen
	cd frontend && npm ci --no-audit --no-fund
	$(PY) -m playwright install chromium || echo "(using pre-installed Playwright browsers)"
	cd frontend && npm run build

setup-compat:           ## clone + build upstream ext-apps basic-host (pinned) for A15
	./scripts/setup_basic_host.sh

build:                  ## compile browser assets, then the wheel + sdist
	cd frontend && npm run build
	rm -rf dist && $(PY) -m build

lint:                   ## ruff + TypeScript type checking
	$(PY) -m ruff check src tests examples scripts
	$(PY) -m ruff format --check src tests examples scripts
	cd frontend && npm run typecheck

test-unit:              ## pytest unit tests + Vitest frontend tests
	$(PYTEST) tests/unit
	cd frontend && npm test

test-integration:       ## starts the fixture + official demo MCP servers
	$(PYTEST) tests/integration

test-e2e:               ## demo/fixture servers + Shiny apps + Playwright (Chromium)
	$(PYTEST) tests/e2e -m "not compat"

test-security:          ## origin, sandbox and unauthorised-tool tests (all layers)
	# Separate runs: sync Playwright and pytest-asyncio cannot share one process loop.
	$(PYTEST) tests/unit tests/integration -m security
	$(PYTEST) tests/e2e -m security

test-compat: setup-compat  ## A15: same official server in upstream basic-host
	REQUIRE_COMPAT=1 $(PYTEST) tests/e2e/test_compat_basic_host.py

test-dist: build        ## A13: install the wheel into a clean venv and check assets
	rm -rf .cache/wheel-venv
	uv venv -q -p 3.11 .cache/wheel-venv
	uv pip install -q -p .cache/wheel-venv dist/*.whl
	cd /tmp && $(CURDIR)/.cache/wheel-venv/bin/python -I $(CURDIR)/scripts/check_wheel.py

test: lint test-unit test-integration test-e2e test-security test-dist test-compat

demo:                   ## official demo MCP server + the ui.Chat example on http://127.0.0.1:8000
	@trap 'kill 0' EXIT; \
	PORT=$(DEMO_PORT) node frontend/node_modules/@modelcontextprotocol/server-basic-vanillajs/dist/index.js & \
	MCP_SERVER_URL=http://127.0.0.1:$(DEMO_PORT)/mcp $(PY) -m shiny run --host 127.0.0.1 --port $(APP_PORT) examples/chat/app.py

demo-standalone:        ## same, with the standalone example
	@trap 'kill 0' EXIT; \
	PORT=$(DEMO_PORT) node frontend/node_modules/@modelcontextprotocol/server-basic-vanillajs/dist/index.js & \
	MCP_SERVER_URL=http://127.0.0.1:$(DEMO_PORT)/mcp $(PY) -m shiny run --host 127.0.0.1 --port $(APP_PORT) examples/standalone/app.py

clean:
	rm -rf dist build test-results .cache/wheel-venv .pytest_cache
