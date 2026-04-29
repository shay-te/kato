PYTHON ?= python3
VENV_PYTHON = .venv/bin/python
KATO_SOURCE_FINGERPRINT := $(shell $(PYTHON) -m kato.helpers.runtime_identity_utils --root .)

.PHONY: bootstrap configure doctor doctor-agent doctor-openhands test run compose-up

bootstrap:
	./scripts/bootstrap.sh

configure:
	$(VENV_PYTHON) scripts/generate_env.py --output .env

doctor:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode all

doctor-agent:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode agent

doctor-openhands:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode openhands

test:
	$(VENV_PYTHON) -m unittest discover -s tests

run:
	./scripts/run-local.sh

compose-up:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	export KATO_SOURCE_FINGERPRINT='$(KATO_SOURCE_FINGERPRINT)'; \
	PROFILES=""; \
	if [ "$${KATO_AGENT_BACKEND:-openhands}" != "claude" ]; then \
		PROFILES="$$PROFILES --profile openhands"; \
	fi; \
	if [ "$${OPENHANDS_SKIP_TESTING:-false}" != "true" ] && [ "$${OPENHANDS_TESTING_CONTAINER_ENABLED:-false}" = "true" ]; then \
		PROFILES="$$PROFILES --profile testing"; \
	fi; \
	docker compose $$PROFILES up --build -d; \
	KATO_CONTAINER_ID=$$(docker compose $$PROFILES ps -q kato); \
	if [ -z "$$KATO_CONTAINER_ID" ]; then \
		echo "unable to determine kato container id"; \
		exit 1; \
	fi; \
	docker attach "$$KATO_CONTAINER_ID"
