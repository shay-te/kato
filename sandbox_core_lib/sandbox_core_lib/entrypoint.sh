#!/bin/bash
# Container entrypoint.
#
# Runs as root just long enough to:
#
#   1. Apply the egress firewall (iptables needs NET_ADMIN), and
#   2. Materialise the per-task ``/home/claude/.claude`` directory
#      from the **read-only** auth source (login mode skips this; see
#      below).
#
# Then drops to the unprivileged ``claude`` user before exec'ing the
# actual Claude CLI. Firewall rules persist in the kernel network
# namespace regardless of which user the process runs as — Claude
# can't undo them because it has no caps.
#
# Two operating modes, distinguished by which mounts the host put in
# place when ``docker run`` was invoked:
#
#   * **spawn mode** (the default for kato task work): the persistent
#     auth volume is mounted **read-only** at ``/auth-src`` and a fresh
#     tmpfs is mounted **read-write** at ``/home/claude/.claude``. We
#     copy a tight allowlist of credential files (``.credentials.json``
#     etc.) from /auth-src → .claude and ignore everything else. This
#     means a previous task that managed to write a poisoned
#     ``settings.json``, hook script, custom slash command, or MCP
#     config into the volume **cannot** influence the current task —
#     we never copy those into the writable dir, and the source mount
#     is read-only so nothing in this task can persist either. The
#     tmpfs is destroyed when the container exits.
#
#   * **login mode** (``make sandbox-login``): no /auth-src mount, the
#     auth volume is mounted **read-write** directly at
#     ``/home/claude/.claude`` so ``claude /login`` can write the
#     credentials the operator just typed. Detected by the absence of
#     /auth-src.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[kato-sandbox] ERROR: entrypoint must start as root for firewall setup" >&2
    exit 1
fi

# Confirm we can actually modify iptables before claiming to firewall.
# Without NET_ADMIN the rule-add silently no-ops on some kernels and
# we'd hand Claude a fully-open container. The probe adds and
# immediately removes a no-op INPUT rule on a private address.
if ! iptables -A INPUT -s 127.255.255.254/32 -j ACCEPT 2>/dev/null; then
    echo "[kato-sandbox] ERROR: cannot modify iptables (NET_ADMIN missing?) — refusing to start without firewall" >&2
    exit 1
fi
iptables -D INPUT -s 127.255.255.254/32 -j ACCEPT 2>/dev/null || true

/usr/local/bin/init-firewall.sh

# ----- materialise /home/claude/.claude -----
#
# spawn mode: copy a strict allowlist out of the read-only auth source
# into the per-task tmpfs at .claude. Anything not on the allowlist
# (settings.json, hooks/, MCP config, commands/, agents/, projects/,
# statsig/, todos/, shell-snapshots/) is intentionally *not* copied,
# so a prior task's poisoned settings cannot influence this task.
#
# login mode: the auth volume is mounted RW directly at .claude, so we
# do nothing — ``claude /login`` will write straight to the volume.

CLAUDE_HOME=/home/claude/.claude
AUTH_SRC=/auth-src
mkdir -p "$CLAUDE_HOME"

if [ -d "$AUTH_SRC" ]; then
    # Allowlist of file basenames safe to carry across tasks. Anything
    # that can run code (hooks, MCP transports, custom slash commands,
    # subagent definitions) is excluded so a poisoned write from a
    # prior task cannot persist its effects into this one.
    SAFE_FILES=(
        ".credentials.json"
        "credentials.json"
    )
    # ----- Bidirectional integrity check -----
    #
    # First refuse the spawn if the auth volume contains anything
    # OTHER than allowlisted files + the manifest. A directory listing
    # must be a strict subset of:
    #
    #     SAFE_FILES + manifest.sha256 + "lost+found"
    #
    # This catches the failure mode the SHA-256 manifest alone misses:
    # an attacker (or a buggy sibling container that somehow got the
    # volume RW) ADDS a new file (e.g. settings.json, hooks/,
    # commands/, .mcp.json) without altering any hashed file. Without
    # this check the manifest would still verify and the malicious
    # file would be available to Claude. ``find -mindepth 1 -maxdepth 1``
    # so we only look at the top level — Claude credentials are flat
    # files; nothing legitimate lives in subdirectories of /auth-src.
    while IFS= read -r path; do
        base=$(basename "$path")
        case "$base" in
            .credentials.json|credentials.json|manifest.sha256|lost+found) ;;
            *)
                echo "[kato-sandbox] ERROR: auth volume contains unexpected entry '$base' — refusing to start. Volume must contain only credential allowlist + manifest. Re-run \`make sandbox-login\` to reset." >&2
                exit 1
                ;;
        esac
    done < <(find "$AUTH_SRC" -mindepth 1 -maxdepth 1)

    # Forward integrity check: every file the manifest claims must
    # match its hash. ``sha256sum -c`` walks the manifest entries.
    if [ -f "$AUTH_SRC/manifest.sha256" ]; then
        if ! (cd "$AUTH_SRC" && sha256sum -c manifest.sha256 --status 2>/dev/null); then
            echo "[kato-sandbox] ERROR: auth volume manifest mismatch — refusing to start. Re-run \`make sandbox-login\` to reset credentials." >&2
            exit 1
        fi
    fi

    # Now copy the allowlisted credential files into the per-task
    # tmpfs. (Order: integrity checks above must succeed FIRST so we
    # never copy a tampered file even momentarily.)
    for f in "${SAFE_FILES[@]}"; do
        if [ -f "$AUTH_SRC/$f" ]; then
            cp -p "$AUTH_SRC/$f" "$CLAUDE_HOME/$f"
        fi
    done
fi

chown -R claude:users "$CLAUDE_HOME" 2>/dev/null \
    || chown -R claude  "$CLAUDE_HOME" 2>/dev/null \
    || true
chmod 700 "$CLAUDE_HOME"

# Drop to the unprivileged user with no inherited or bounding-set
# capabilities. Even if a setuid binary somehow lands inside (none do
# in this image), the bounding-set wipe makes it impossible to gain
# back NET_ADMIN to tamper with the firewall.
exec setpriv \
    --reuid=claude \
    --regid=100 \
    --init-groups \
    --inh-caps=-all \
    --bounding-set=-all \
    -- "$@"
