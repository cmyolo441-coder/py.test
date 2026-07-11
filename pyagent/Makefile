.PHONY: install dev test lint run health tools clean

install:
	pip install -r requirements.txt

dev:
	pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	ruff check agent tests

run:
	python main.py

health:
	python scripts/healthcheck.py

tools:
	python scripts/list_tools.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache build dist *.egg-info
