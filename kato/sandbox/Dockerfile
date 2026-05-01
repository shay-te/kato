# Hardened Claude CLI sandbox.
#
# Carries an explicit ``org.kato.sandbox`` label so the host-side
# manager can verify the image cached as ``kato/claude-sandbox:latest``
# was actually built from THIS Dockerfile, not a same-named image
# someone else dropped into the local Docker cache.
#
# Built lazily by ``kato.sandbox.manager.ensure_image`` on the first
# spawn after ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` boot. Cached
# locally as ``kato/claude-sandbox:latest`` for every subsequent run.
#
# What's inside:
#   - Node 20 (required by the Claude CLI)
#   - The Claude CLI itself, pinned at the latest release at build time
#   - iptables + ipset for the egress firewall
#   - setpriv (util-linux) so the entrypoint can drop root after
#     applying the firewall
#   - sudo so the unprivileged user can re-run the firewall script if
#     ever needed (it doesn't, but useful for debugging)
#
# What's NOT inside:
#   - git (Claude is forbidden from git operations — kato runs them)
#   - SSH client / keys
#   - Docker CLI (no nested escapes)
#   - Build tools / compilers (not needed for editing files)

FROM node:22-bookworm-slim

LABEL org.kato.sandbox="true" \
      org.kato.sandbox.purpose="Hardened Claude CLI sandbox for KATO_CLAUDE_BYPASS_PERMISSIONS=true." \
      org.kato.sandbox.allowlist="api.anthropic.com:443"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iptables \
        ipset \
        dnsutils \
        ca-certificates \
        curl \
        jq \
        sudo \
        util-linux \
    && apt-get upgrade -y \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Claude Code CLI. We pin to ``latest`` at image-build time and disable
# the in-CLI auto-updater at runtime (the firewall would block its
# fetch anyway, but the explicit env var stops it from trying).
RUN npm install -g @anthropic-ai/claude-code

# Non-root user for the actual Claude process. Uid 1000 matches the
# typical host developer uid so bind-mounted workspace files don't
# come out as root-owned on the host. The ``node:20-bookworm-slim``
# base ships with a pre-existing ``node`` user at uid 1000; remove it
# first so we can hand uid 1000 to ``claude``.
RUN userdel --remove node 2>/dev/null || true \
    && useradd --create-home --shell /bin/bash --uid 1000 --gid 100 claude

# Firewall init runs as root with NET_ADMIN; entrypoint drops to
# ``claude`` user before exec'ing the CLI.
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
COPY entrypoint.sh    /usr/local/bin/entrypoint.sh
RUN chown root:root /usr/local/bin/init-firewall.sh /usr/local/bin/entrypoint.sh \
    && chmod 755    /usr/local/bin/init-firewall.sh /usr/local/bin/entrypoint.sh

# Workspace bind mount target. Read-only image FS means everything
# else writes to the per-task volume mounts only.
WORKDIR /workspace

# Telemetry / auto-update off by default. Kato keeps the CLI updated
# by rebuilding the image, not by letting the in-container CLI fetch.
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENV DISABLE_AUTOUPDATER=1
ENV CLAUDE_CONFIG_DIR=/home/claude/.claude

# Container starts as root so init-firewall.sh can lay down the
# iptables policy. ``entrypoint.sh`` drops to ``claude`` immediately
# after, so no Claude code ever runs with privileges.
USER root
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
