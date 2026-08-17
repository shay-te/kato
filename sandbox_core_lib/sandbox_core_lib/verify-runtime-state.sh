#!/bin/bash
# Last gate before the agent runs: verify the state we ACHIEVED, not the
# flags we requested.
#
# Everything else in this sandbox asserts intent — a frozenset of required
# docker flags, a doc anchor, an argv check. All of those can agree with
# each other and still be wrong about the container the daemon actually
# built. Three separate flags in this sandbox were rejected outright by
# the daemon while every intent-level check stayed green, and
# ``apparmor=docker-default`` is silently a no-op on any host without
# AppArmor loaded, which nothing noticed.
#
# This script runs as the FINAL step of the privilege drop: the entrypoint
# execs ``setpriv ... -- verify-runtime-state.sh <agent command>``, so the
# process reading /proc/self/status here is the exact process that will
# become the agent. It fails closed on anything it can decide, reports
# loudly on anything it cannot, and only then execs the agent.
set -euo pipefail
IFS=$'\n\t'

STATUS=/proc/self/status
EXPECTED_UID=1000
EXPECTED_GID=100
EMPTY_CAPS=0000000000000000

fail() {
    echo "[kato-sandbox] FATAL: runtime state verification failed — $*" >&2
    echo "[kato-sandbox]   Refusing to start the agent. The container was" >&2
    echo "[kato-sandbox]   not built with the isolation the sandbox claims." >&2
    exit 1
}

field() { awk -v key="^$1:" '$0 ~ key {print $2; exit}' "$STATUS"; }

[ -r "$STATUS" ] || fail "cannot read $STATUS"

# ----- identity: we must be the unprivileged agent user -----
actual_uid=$(field Uid)
actual_gid=$(field Gid)
[ "$actual_uid" = "$EXPECTED_UID" ] \
    || fail "running as uid $actual_uid, expected $EXPECTED_UID (privilege drop did not happen)"
[ "$actual_gid" = "$EXPECTED_GID" ] \
    || fail "running as gid $actual_gid, expected $EXPECTED_GID"

# ----- capabilities: every set must be empty, bounding set included -----
# CapBnd is the one that matters most: a non-empty bounding set means a
# regained capability is still reachable (e.g. NET_ADMIN to rewrite the
# egress firewall from inside).
for cap_field in CapInh CapPrm CapEff CapBnd CapAmb; do
    value=$(field "$cap_field")
    [ -n "$value" ] || continue          # kernel without this field
    [ "$value" = "$EMPTY_CAPS" ] \
        || fail "$cap_field=$value, expected $EMPTY_CAPS (capabilities survived the drop)"
done

# ----- no-new-privileges: blocks regaining privilege via setuid exec -----
nnp=$(field NoNewPrivs)
[ "$nnp" = "1" ] || fail "NoNewPrivs=${nnp:-<absent>}, expected 1"

# ----- seccomp: mode 2 == SECCOMP_MODE_FILTER -----
# Decidable → fail closed. Absent → say so rather than imply a check
# happened: gVisor's procfs does not always expose this field, and a
# silent skip is exactly the kind of unverified assumption this script
# exists to remove.
if grep -q '^Seccomp:' "$STATUS"; then
    seccomp=$(field Seccomp)
    [ "$seccomp" = "2" ] \
        || fail "Seccomp mode $seccomp, expected 2 (SECCOMP_MODE_FILTER) — the pinned profile is not active"
    seccomp_state="filter (mode 2)"
else
    seccomp_state="UNVERIFIABLE (no Seccomp field in $STATUS)"
    echo "[kato-sandbox] WARN: cannot verify seccomp mode — $STATUS has no Seccomp field." >&2
fi

# ----- LSM: report, never fail -----
# AppArmor is requested with ``--security-opt apparmor=docker-default``,
# which Docker silently ignores on hosts where AppArmor is not loaded
# (macOS, many distros). That is an accepted residual, but an UNREPORTED
# one is how a layer everyone believes in turns out to have never been
# enforced. Name the real state on every start.
lsm_state='unknown'
if [ -r /proc/self/attr/current ]; then
    lsm_state=$(tr -d '\0' < /proc/self/attr/current 2>/dev/null || echo unknown)
    case "$lsm_state" in
        docker-default*|kato*)
            ;;
        unconfined*|''|unknown)
            echo "[kato-sandbox] WARN: AppArmor is NOT enforcing (profile: ${lsm_state:-none})." >&2
            echo "[kato-sandbox]   apparmor=docker-default was requested but this host" >&2
            echo "[kato-sandbox]   has no AppArmor — the other layers still apply." >&2
            ;;
        *)
            echo "[kato-sandbox] LSM profile: $lsm_state" >&2
            ;;
    esac
fi

echo "[kato-sandbox] runtime state verified: uid=$actual_uid gid=$actual_gid" \
     "caps=empty nnp=1 seccomp=$seccomp_state lsm=${lsm_state:-none}"

exec "$@"
