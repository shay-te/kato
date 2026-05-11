FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# Docker is for the OpenHands backend only. The Claude backend has to run on
# the host (KATO_AGENT_BACKEND=claude is rejected at startup inside Docker)
# because the Claude CLI authenticates against `claude login` credentials in
# the host's keychain, which a container cannot reach.
RUN sh /app/scripts/install-python-deps.sh python && \
    apt-get update && \
    apt-get install -y --no-install-recommends git openssh-client && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /app/docker/entrypoint-run.sh
