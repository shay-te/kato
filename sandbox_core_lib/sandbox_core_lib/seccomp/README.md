# Vendored seccomp profile

`default.json` is Docker's own default seccomp profile, vendored verbatim.

| | |
|---|---|
| Source | `moby/moby` → `profiles/seccomp/default.json` |
| Version | tag `v24.0.9` |
| Default action | `SCMP_ACT_ERRNO` (deny unless listed) |

## Why vendor it instead of inheriting the daemon default

Passing no `--security-opt seccomp=` flag makes the container run under
*whatever this daemon calls default* — and that is host-settable:

```
dockerd --seccomp-profile /some/weak.json
```

A host configured that way silently weakens every sandbox, and a check
that only asks "is it `unconfined`?" stays green because the flag is
simply absent. Pinning a file we ship makes the enforced syscall set a
property of this lib, identical on every host.

`--security-opt seccomp=builtin` says the same thing in one word, but it
only exists on Docker >= 25. Older daemons read `builtin` as a
*filename* and every spawn dies with:

```
docker: opening seccomp profile (builtin) failed: open builtin: no such file or directory
```

The vendored file works on every supported daemon.

## Updating it

1. Copy the profile from the moby tag you want to track.
2. Update the Version row above.
3. Re-run the sandbox suite, then verify the two privileged paths still
   work under the new profile — they are the ones a stricter profile
   would break first:

```bash
P=sandbox_core_lib/sandbox_core_lib/seccomp/default.json
# firewall init (needs NET_ADMIN + iptables syscalls)
docker run --rm --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW \
  --security-opt seccomp=$P --entrypoint iptables kato/claude-sandbox:latest -L -n
# privilege drop (needs setresuid/setgroups)
docker run --rm --cap-drop ALL --cap-add SETUID --cap-add SETGID \
  --security-opt seccomp=$P --entrypoint setpriv kato/claude-sandbox:latest \
  --reuid=claude --regid=100 --init-groups --inh-caps=-all --bounding-set=-all -- id
```

Do not edit `default.json` by hand. A local tweak here is invisible to
everything that reads the upstream profile, and the drift is impossible
to spot in review.
