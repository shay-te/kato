"""Derive the agent seccomp profile from the vendored Docker baseline.

``default.json`` is Docker's own profile, vendored verbatim (see
README.md). It is a good BASELINE but it is written for containers in
general, so it permits syscalls that a coding agent has no use for and
that carry real kernel attack surface.

This script produces ``agent.json`` = baseline MINUS the denylist below.
Keeping it a derivation rather than a hand-edited copy means the delta is
one readable list instead of a 800-line diff, and
``test_agent_seccomp_profile.py`` re-runs this to prove the checked-in
file still equals baseline-minus-denylist — so a future baseline refresh
cannot silently drop a denial.

Run: python -m sandbox_core_lib.sandbox_core_lib.seccomp.derive_agent_profile
"""
from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
BASELINE_PATH = _HERE / 'default.json'
AGENT_PATH = _HERE / 'agent.json'

# Syscalls removed from the baseline. Each line says what it buys an
# attacker; none is reachable in normal Claude/node/git operation, which
# the profile test verifies against the real image.
DENIED_SYSCALLS = {
    # Process inspection/injection. Same-uid ptrace is enough to read
    # another process's memory — in this container that means the agent
    # reading the credentials of anything else running beside it.
    'ptrace',
    'process_vm_readv',
    'process_vm_writev',
    # eBPF: kernel-side programs; a long tail of privilege-escalation
    # CVEs and an excellent primitive for reading kernel memory.
    'bpf',
    # Userfaultfd is the standard tool for winning kernel race conditions
    # by pausing a page fault at a chosen instant.
    'userfaultfd',
    # Kernel keyring — credential storage the agent never touches.
    'keyctl',
    'add_key',
    'request_key',
    # perf: kernel profiling interface, historically a rich CVE source.
    'perf_event_open',
    # kcmp compares kernel objects across processes — an ASLR/side-channel
    # oracle. lookup_dcookie resolves kernel dentry cookies from the perf
    # subsystem. Neither has any use here.
    'kcmp',
    'lookup_dcookie',
}


def derive(baseline: dict) -> dict:
    """Baseline with ``DENIED_SYSCALLS`` removed from every allow rule."""
    profile = json.loads(json.dumps(baseline))          # deep copy
    kept_rules = []
    for rule in profile.get('syscalls', []):
        names = [n for n in rule.get('names', []) if n not in DENIED_SYSCALLS]
        if not names:
            # Whole rule was denied syscalls — drop it rather than leave
            # an empty rule the kernel would reject.
            continue
        rule['names'] = names
        kept_rules.append(rule)
    profile['syscalls'] = kept_rules
    return profile


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
    agent = derive(baseline)
    AGENT_PATH.write_text(
        json.dumps(agent, indent=1, sort_keys=False) + '\n', encoding='utf-8',
    )
    before = sum(len(r.get('names', [])) for r in baseline.get('syscalls', []))
    after = sum(len(r.get('names', [])) for r in agent.get('syscalls', []))
    print(f'{AGENT_PATH.name}: {after} syscalls allowed ({before - after} removed)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
