# Hardened Claude CLI sandbox.
#
# Carries an explicit ``org.kato.sandbox`` label so the host-side
# manager can verify the image cached as ``kato/claude-sandbox:latest``
# was actually built from THIS Dockerfile, not a same-named image
# someone else dropped into the local Docker cache. Note that the
# label check is non-cryptographic: a determined attacker with local
# Docker access can tag any image with our label. Operators concerned
# about supply-chain attacks at this level should pin the base image
# by digest (replace ``node:22-bookworm-slim`` with
# ``node:22-bookworm-slim@sha256:<digest>``) and host their own
# image registry.
#
# Built lazily by ``kato.sandbox.manager.ensure_image`` on the first
# spawn after ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` boot. Cached
# locally as ``kato/claude-sandbox:latest`` for every subsequent run.
#
# What's inside:
#   - Node 22 (required by the Claude CLI)
#   - The Claude CLI itself, pinned at the latest release at build time
#   - iptables + ipset for the egress firewall (incl. hashlimit module
#     for DNS rate limiting — see init-firewall.sh)
#   - setpriv (util-linux) so the entrypoint can drop root after
#     applying the firewall
#
# What's NOT inside (intentional reductions to attack surface):
#   - sudo (not needed; entrypoint uses ``setpriv`` for the one-time
#     root → claude transition, and Claude can't get back to root
#     once the bounding-set is wiped)
#   - git (Claude is forbidden from git operations — kato runs them)
#   - SSH client / keys
#   - Docker CLI (no nested escapes)
#   - Build tools / compilers (not needed for editing files)

# Base image is overridable via build-arg so operators concerned with
# supply-chain attacks can pin to an exact digest:
#
#   docker build --build-arg BASE_IMAGE=node:22-bookworm-slim@sha256:<digest> ...
#
# (or set ``KATO_SANDBOX_BASE_IMAGE`` and let ``manager.build_image``
# pass it through). The label-based identity check is not a substitute
# for digest pinning — a same-named local image can be tagged with our
# label by anyone with local Docker access.
ARG BASE_IMAGE=node:22-bookworm-slim
FROM ${BASE_IMAGE}

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
        util-linux \
        coreutils \
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
# come out as root-owned on the host. The ``node:22-bookworm-slim``
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
