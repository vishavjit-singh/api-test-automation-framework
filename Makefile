PY ?= python3
ENV ?= inproc

.PHONY: install test test-unit test-api smoke serve demo report catalogue xray-dry-run lint up down clean

install:
	$(PY) -m pip install -r requirements-dev.txt

## Framework unit tests + the API suite in-process (no server needed)
test:
	$(PY) -m pytest -q

test-unit:
	$(PY) -m pytest -q tests/unit

## The API suite against any environment: make test-api ENV=local|docker|staging
test-api:
	$(PY) -m pytest -q tests/api --env $(ENV) --junitxml reports/junit.xml

smoke:
	$(PY) -m pytest -q tests/api --env $(ENV) -m smoke

serve:
	$(PY) -m demo_service

## End to end over real HTTP: start the service, run the suite, write reports, charts and the catalogue
demo:
	rm -rf reports
	$(PY) tools/serve_and_test.py
	$(PY) -m apitest report --out docs/images
	$(MAKE) catalogue
	$(PY) -m apitest xray-upload

report:
	$(PY) -m apitest report --out docs/images

catalogue:
	$(PY) -m pytest tests/api --collect-only -q -p no:cacheprovider --catalogue docs/TEST_CATALOGUE.md > /dev/null

xray-dry-run:
	$(PY) -m apitest xray-upload

lint:
	$(PY) -m ruff check .

up:
	mkdir -p reports && chmod 777 reports
	docker compose up --build --exit-code-from tests

down:
	docker compose down -v

clean:
	rm -rf reports
