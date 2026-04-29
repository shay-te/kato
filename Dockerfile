FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# Install Python deps, git/ssh for repository ops, and Node.js + Claude Code
# CLI so the `claude` backend (KATO_AGENT_BACKEND=claude) works inside the
# container. The Claude CLI authenticates from ANTHROPIC_API_KEY at runtime.
# A non-root `kato` user is created because Claude CLI refuses to run with
# `--dangerously-skip-permissions` (i.e. permission_mode=bypassPermissions)
# under root.
RUN sh /app/scripts/install-python-deps.sh python && \
    apt-get update && \
    apt-get install -y --no-install-recommends git openssh-client curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /app/docker/entrypoint-run.sh && \
    useradd --create-home --uid 1000 --shell /bin/bash kato && \
    chown -R kato:kato /app

USER kato
