#!/bin/bash
# Container entrypoint.
#
# Runs as root just long enough to apply the egress firewall (iptables
# needs NET_ADMIN), then drops to the unprivileged ``claude`` user
# before exec'ing the actual Claude CLI. The firewall rules persist
# in the kernel network namespace regardless of which user the
# process runs as — Claude can't undo them because it has no caps.

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

# Auth volume may come up empty on first mount — make sure the
# directory exists with claude-user ownership so the CLI can write
# its credentials/session files.
mkdir -p /home/claude/.claude
chown -R claude:users /home/claude/.claude 2>/dev/null \
    || chown -R claude /home/claude/.claude 2>/dev/null \
    || true

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
