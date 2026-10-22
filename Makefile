PY ?= python3
VENV ?= .venv
SAMPLE := examples/sample-report/index.html

.PHONY: install test demo lint clean report

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e .
	@echo "installed: $(VENV)/bin/lacuna"

test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

demo:
	PYTHONPATH=src $(PY) -m lacuna demo -o $(SAMPLE)

report:
	PYTHONPATH=src $(PY) -m lacuna report -o lacuna-report.html

lint:
	@if command -v ruff >/dev/null 2>&1; then ruff check src tests; else echo "ruff not installed, skipping"; fi

clean:
	rm -rf build dist .ruff_cache $(VENV) src/*.egg-info lacuna-report.html
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
