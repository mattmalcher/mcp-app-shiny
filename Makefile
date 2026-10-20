# shiny-mcp-host developer commands. Requires uv; setup-node and the JS targets also need Node 20+.
PY        := .venv/bin/python
PYTEST    := $(PY) -m pytest
DEMO_PORT ?= 3001
APP_PORT  ?= 8000

.PHONY: setup setup-dev setup-node vendor-sdk setup-compat build lint test-unit test-integration test-e2e test-security \
        test-compat test-dist demo-server-bundle connect-bundles rehearse-connect rehearse-connect-trusted test demo demo-clock \
        demo-standalone clean

setup:                  ## Python env from uv.lock: all you need for `make demo` (no Node)
	uv sync --frozen

setup-dev: setup        ## + Playwright Chromium: enough for the Python test suites (no Node)
	$(PY) -m playwright install chromium || echo "(using pre-installed Playwright browsers)"

setup-node:             ## Node packages + browser builds: TypeScript, Vitest, official-server tests (Node 20+)
	cd frontend && npm ci --no-audit --no-fund
	cd frontend && npm run build

vendor-sdk:             ## re-vendor the official MCP Apps SDK (version from frontend/package.json); no Node
	$(PY) scripts/vendor_app_sdk.py

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

demo-server-bundle:     ## package the sales explorer as a self-contained MCP server folder in dist/
	$(PY) scripts/package_demo_server.py

connect-bundles:        ## the three Posit Connect deployment folders in dist/connect (docs/deployment.md)
	$(PY) scripts/package_connect.py

rehearse-connect: connect-bundles  ## run those folders locally from clean venvs, then scripts/check_deployment.py
	$(PY) scripts/rehearse_connect.py

rehearse-connect-trusted: connect-bundles  ## the same, in trusted mode: MCP server + Shiny app only, no sandbox
	$(PY) scripts/rehearse_connect.py --trusted

test: lint test-unit test-integration test-e2e test-security test-dist test-compat

demo:                   ## sales explorer showcase: MCP server + sandbox + Shiny chat app on http://127.0.0.1:8000
	$(PY) scripts/demo.py sales

demo-clock:             ## smoke test: official "Get Server Time" app in ui.Chat
	$(PY) scripts/demo.py clock

demo-standalone:        ## official "Get Server Time" app in a plain Shiny page
	$(PY) scripts/demo.py standalone

clean:
	rm -rf dist build test-results .cache/wheel-venv .cache/connect-rehearsal .pytest_cache
