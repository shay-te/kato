PYTHON ?= python3
VENV_PYTHON = .venv/bin/python
KATO_SOURCE_FINGERPRINT := $(shell $(PYTHON) -m kato_core_lib.helpers.runtime_identity_utils --root .)

.PHONY: bootstrap configure doctor doctor-agent doctor-openhands test run compose-up compose-up-docker sandbox-build sandbox-login sandbox-verify

# All operator-facing entry points are canonical Python scripts so the
# behavior matches what Windows operators see (`python scripts\<name>.py`).
# The `.sh` files are POSIX-only wrappers; we skip them here.

bootstrap:
	$(PYTHON) scripts/bootstrap.py

configure:
	$(VENV_PYTHON) scripts/generate_env.py --output .env

doctor:
	$(VENV_PYTHON) -m kato_core_lib.validate_env --env-file .env --mode all

doctor-agent:
	$(VENV_PYTHON) -m kato_core_lib.validate_env --env-file .env --mode agent

doctor-openhands:
	$(VENV_PYTHON) -m kato_core_lib.validate_env --env-file .env --mode openhands

test:
	$(VENV_PYTHON) -m unittest discover -s tests

run:
	$(VENV_PYTHON) scripts/run_local.py

build-agent-server:
	docker build -t kato-agent-server:$${KATO_AGENT_SERVER_IMAGE_TAG:-1.12.0-python} docker/agent-server

compose-up:
	$(VENV_PYTHON) scripts/run_local.py

# Build the hardened Claude sandbox image up-front. Kato also builds
# it lazily on the first sandboxed spawn, so this target is optional —
# useful if you want to pre-warm the cache or surface build errors
# before starting kato.
sandbox-build:
	$(VENV_PYTHON) -c "from kato_core_lib.sandbox.manager import build_image; build_image()"

# One-time interactive login for the sandbox. Seeds the persistent
# ``kato-claude-config`` Docker volume with the operator's Claude
# credentials so kato-spawned sandbox containers can reuse them.
# Skip if you set ANTHROPIC_API_KEY in your shell instead.
sandbox-login:
	$(VENV_PYTHON) -c "from kato_core_lib.sandbox.manager import ensure_image, login_command, stamp_auth_volume_manifest; import subprocess, sys; ensure_image(); rc = subprocess.call(login_command()); stamp_auth_volume_manifest() if rc == 0 else None; sys.exit(rc)"

# End-to-end smoke test: builds the image, spins up a throwaway
# container, asserts every protection (uid drop, cap drop, read-only
# rootfs, IPv6 disabled, DNS pinned, allowed/blocked egress), and
# tears down. Run this before relying on the sandbox in production
# and any time the Dockerfile / firewall script changes.
sandbox-verify:
	$(VENV_PYTHON) -m kato_core_lib.sandbox.verify

# Original docker-compose flow. Kept available for cases where you actually
# need OpenHands containerized; the local Claude-backed path is `compose-up`.
compose-up-docker:
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
