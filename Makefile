# Retrace — developer convenience targets.
# Requires: macOS, Xcode toolchain (swiftc), Python 3.11+, and uv.

PY := uv run

.PHONY: help setup build-helpers run mcp tick doctor init test lint clean package

help:
	@echo "Retrace make targets:"
	@echo "  setup          Create the venv (Python 3.13) and install (editable)."
	@echo "  build-helpers  Compile the Swift helper binaries into ~/.retrace/bin."
	@echo "  init           Create ~/.retrace, default config, and the database."
	@echo "  run            Start the HTTP API + web UI."
	@echo "  mcp            Start the read-only MCP server (stdio)."
	@echo "  tick           Run a single capture cycle now."
	@echo "  doctor         Check permissions and native capabilities."
	@echo "  test           Run the test suite."
	@echo "  clean          Remove build artifacts and caches."

setup:
	uv venv --python 3.13
	uv pip install -e ".[dev]"

build-helpers:
	$(PY) python -m retrace.native.helpers --build-all

init:
	$(PY) retrace init

run:
	$(PY) retrace serve

mcp:
	$(PY) retrace mcp

tick:
	$(PY) retrace tick

doctor:
	$(PY) retrace doctor

test:
	$(PY) pytest

lint:
	$(PY) python -m compileall retrace

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

package:
	uv build
