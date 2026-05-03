"""End-to-end sandbox smoke test.

Run via ``make sandbox-verify``. Builds the image (if missing) and
spins up a single throwaway container that asserts every protection
the sandbox is supposed to provide. Tears down on exit. Prints a
clear PASS/FAIL line per check and exits non-zero on any failure.

Checks performed inside the container, in this order:

1. Process runs as the unprivileged ``claude`` user (uid 1000).
2. Capability bounding set is empty (cannot regain NET_ADMIN etc).
3. Root filesystem is read-only (cannot write outside the workspace).
4. ``/workspace`` exists and is writable.
5. ``/etc/resolv.conf`` lists the pinned Cloudflare resolvers only.
6. IPv6 is disabled at the sysctl level.
7. ``api.anthropic.com`` is reachable on TCP/443 (firewall allowed).
8. ``example.com`` is **not** reachable (firewall blocking).
9. ``github.com`` is **not** reachable (firewall blocking).
10. Outbound DNS to a non-pinned resolver fails (firewall blocking).
11. ``/auth-src`` is read-only (writes must fail with EROFS).
12. ``/home/claude/.claude`` is on tmpfs (per-task ephemeral, not a
    persistent volume) and writes by Claude do **not** persist back
    to the auth volume.
13. Cloud metadata IP (169.254.169.254) is firewall-blocked.
14. RFC1918 host-bridge IPs (e.g. 172.17.0.1) are firewall-blocked.

The verification command runs as the same ``claude`` user the real
spawn uses, so anything it can't do, the real Claude can't either.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from kato_core_lib.sandbox.manager import (
    SANDBOX_IMAGE_TAG,
    SandboxError,
    docker_available,
    ensure_image,
    ensure_network,
    wrap_command,
)


# In-container shell script that asserts every protection. Each ``check``
# prints PASS/FAIL on stdout; we count failures on the host side. The
# script avoids `set -e` so a single failure doesn't suppress later
# checks — operator wants the full picture, not "first failure wins".
_VERIFICATION_SCRIPT = r"""
set -u
fail_count=0

check() {
    local name="$1"; shift
    if "$@" >/tmp/check.out 2>&1; then
        echo "[verify] PASS  $name"
    else
        echo "[verify] FAIL  $name"
        sed 's/^/[verify]    | /' /tmp/check.out >&2
        fail_count=$((fail_count + 1))
    fi
}

check "runs as uid 1000 (claude user)" bash -c 'test "$(id -u)" -eq 1000'
check "no caps in bounding set" bash -c '
    if command -v capsh >/dev/null 2>&1; then
        bounding=$(capsh --print 2>/dev/null | grep "Bounding set" | sed "s/.*=//")
        test -z "$bounding"
    else
        # capsh not present: probe by trying a NET_ADMIN op (must fail).
        ! iptables -L >/dev/null 2>&1
    fi
'
check "rootfs is read-only" bash -c '! touch /etc/_kato_verify_probe 2>/dev/null'
check "/workspace is writable" bash -c '
    touch /workspace/_kato_verify_probe &&
    rm -f /workspace/_kato_verify_probe
'
check "DNS resolver is Docker-managed (forwards to pinned upstream)" bash -c '
    # On a custom bridge network Docker uses its embedded resolver
    # (127.0.0.11) which forwards to the upstream specified in
    # ``--dns 1.1.1.1 --dns 1.0.0.1`` on the host side. The actual
    # *security* property — that DNS only works via the pinned
    # upstreams — is enforced by the iptables allowlist and
    # asserted by the "DNS to non-pinned resolver BLOCKED" check
    # below. Here we only verify the resolver is Docker-managed
    # (not a hostfile bypass or a leaked external resolver).
    grep -qE "nameserver (127\.0\.0\.11|1\.1\.1\.1|1\.0\.0\.1)" /etc/resolv.conf
'
check "IPv6 disabled at sysctl" bash -c '
    test "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null)" = "1"
'
check "api.anthropic.com reachable" bash -c '
    curl --connect-timeout 8 --max-time 12 -sI https://api.anthropic.com/ \
        -o /dev/null -w "%{http_code}\n" >/tmp/ac.out 2>&1 &&
    grep -qE "^(2|3|4)[0-9][0-9]$" /tmp/ac.out
'
check "example.com BLOCKED by firewall" bash -c '
    ! curl --connect-timeout 5 --max-time 8 -sI https://example.com/ \
        -o /dev/null 2>/dev/null
'
check "github.com BLOCKED by firewall" bash -c '
    ! curl --connect-timeout 5 --max-time 8 -sI https://github.com/ \
        -o /dev/null 2>/dev/null
'
check "DNS to non-pinned resolver (8.8.8.8) BLOCKED" bash -c '
    ! timeout 5 dig @8.8.8.8 example.com +short +timeout=3 +tries=1 >/dev/null 2>&1
'
# Stronger "only api.anthropic.com:443" assertions — prove the allowlist
# is tight in every dimension (port, neighbor IPs, plaintext, alt ports).
check "TCP/443 to Google (8.8.8.8) BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/8.8.8.8/443" 2>/dev/null
'
check "TCP/443 to Cloudflare (1.1.1.1) BLOCKED (DNS-only resolver)" bash -c '
    ! timeout 5 bash -c "</dev/tcp/1.1.1.1/443" 2>/dev/null
'
check "TCP/22 (SSH) outbound BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/api.anthropic.com/22" 2>/dev/null
'
check "TCP/8080 outbound BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/api.anthropic.com/8080" 2>/dev/null
'
check "plain HTTP (port 80) to api.anthropic.com BLOCKED" bash -c '
    ! curl --connect-timeout 5 --max-time 8 -sI http://api.anthropic.com/ \
        -o /dev/null 2>/dev/null
'
# ----- new layer-0 protections (auth volume isolation, metadata, RFC1918) -----
check "auth source mount /auth-src is READ-ONLY" bash -c '
    test -d /auth-src && ! touch /auth-src/_kato_verify_probe 2>/dev/null
'
check "/home/claude/.claude is on tmpfs (per-task ephemeral)" bash -c '
    fs=$(stat -f -c %T /home/claude/.claude 2>/dev/null \
         || stat --file-system --format=%T /home/claude/.claude 2>/dev/null)
    test "$fs" = "tmpfs"
'
check ".claude is writable by claude user (own state, not persistent)" bash -c '
    touch /home/claude/.claude/_probe && rm -f /home/claude/.claude/_probe
'
check "AWS/GCP cloud metadata IP (169.254.169.254) BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/169.254.169.254/80" 2>/dev/null
'
check "RFC1918 host-bridge IP (172.17.0.1) BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/172.17.0.1/80" 2>/dev/null
'
check "private LAN IP (192.168.1.1) BLOCKED" bash -c '
    ! timeout 5 bash -c "</dev/tcp/192.168.1.1/80" 2>/dev/null
'
check "DNS rate limit eventually drops bursts (>60/min)" bash -c '
    # Fire ~120 unique queries in a tight loop and confirm at least
    # one is dropped (timeout on dig). Hashlimit grants a burst of 20
    # then drops at the limit, so within 120 queries we expect drops.
    drops=0
    for i in $(seq 1 120); do
        timeout 1 dig @1.1.1.1 "kato-rl-$i.example.invalid" +tries=1 +time=1 +short >/dev/null 2>&1 || drops=$((drops+1))
    done
    test "$drops" -gt 0
'

if [ "$fail_count" -gt 0 ]; then
    echo "[verify] $fail_count check(s) failed" >&2
    exit 1
fi
echo "[verify] all checks passed"
"""


def main() -> int:
    if not docker_available():
        sys.stderr.write(
            'kato sandbox verify: docker is not available. Install Docker '
            'and start the daemon, then re-run.\n',
        )
        return 1
    print('[verify] preparing sandbox image...', flush=True)
    try:
        ensure_image()
    except SandboxError as exc:
        sys.stderr.write(f'kato sandbox verify: image build failed: {exc}\n')
        return 1
    ensure_network()

    # Throwaway workspace — the verifier doesn't write production files,
    # but ``wrap_command`` needs an existing directory to bind-mount.
    with tempfile.TemporaryDirectory(prefix='kato-sandbox-verify-') as workspace:
        argv = wrap_command(
            inner_command=['bash', '-c', _VERIFICATION_SCRIPT],
            workspace_path=workspace,
            container_name=f'kato-sandbox-verify-{Path(workspace).name[-8:]}',
        )
        print('[verify] launching verification container...', flush=True)
        try:
            result = subprocess.run(argv, timeout=120)
        except subprocess.TimeoutExpired:
            sys.stderr.write(
                'kato sandbox verify: container ran longer than 120s — aborting.\n',
            )
            return 1
        except OSError as exc:
            sys.stderr.write(f'kato sandbox verify: docker run failed: {exc}\n')
            return 1
    if result.returncode == 0:
        print('[verify] sandbox verification passed.')
    else:
        sys.stderr.write(
            f'[verify] sandbox verification FAILED (exit {result.returncode}). '
            'See PASS/FAIL list above.\n',
        )
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
