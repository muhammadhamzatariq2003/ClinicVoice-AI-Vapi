.PHONY: install dev run demo test lint lock docker

install:        ## runtime dependencies
	pip install -r requirements.txt

dev:            ## runtime + test tooling
	pip install -r requirements-dev.txt

run:            ## start the webhook server
	python -m app.main

demo:           ## offline scenario replay, no accounts needed
	python scripts/run_scenarios.py

test:
	python -m pytest

lint:
	ruff check .

lock:           ## freeze the exact versions you just installed
	pip freeze > requirements.lock

docker:
	docker compose up --build
