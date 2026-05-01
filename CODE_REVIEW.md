# Code review — kato

A single-pass review of the entire kato codebase as it stands at the end of this work session. Findings are ranked by severity. Things that are *fine* aren't listed — the goal is to surface what's worth your attention, not to celebrate what works.

---

## Severity 1 — broken or wrong

None known. The two real bugs found this session were fixed:
- `kato_core_lib.py` was reading `open_cfg.kato.review_workspace_ttl_seconds` when `open_cfg` is already the kato block. **Fixed.**
- `install_status_broadcast_handler` only attached to the root logger; `kato.workflow` has `propagate=False` so workflow records never reached the broadcaster, and the planning UI's status bar showed `Live feed connected. Waiting for the first scan tick.` while the terminal was full of activity. **Fixed; regression test added.**

---

## Severity 2 — risky behavior worth a second look

### S2.1 — In-memory `_pending_publish` survives only the running process
[`AgentService.approve_push`](kato/data_layers/service/agent_service.py) stashes the post-test execution context in a `dict` so the operator's "Approve push" button can resume publish without re-running the agent. **A kato restart loses every pending approval.** The branch + commits survive on disk; the operator's path to recovery is to remove the `kato:wait-before-git-push` tag and let kato re-process the task. That works but it forces the agent to re-run.

**Fix options if this hits operators in practice:**
- Persist the pending state to `~/.kato/pending-publish/<task-id>.json` with enough info to reconstruct (task, prepared_task summary, execution payload).
- Or detect on boot that a workspace exists with status `review` for a task still tagged `kato:wait-before-git-push`, and re-derive a publish path from disk.

### S2.2 — `.env.example` claim about Docker now obsolete
The `KATO_AGENT_BACKEND=claude` flow refuses to start inside Docker (`ClaudeCliClient.validate_connection` raises). The `.env.example` and README accurately reflect this. The `compose-up-docker` Make target only builds OpenHands. No drift detected, but worth flagging: if you ever do wire Claude-in-devcontainer, the no-Docker check will need to relax for that case.

### S2.3 — Synthetic first SSE entry uses `sequence=-1`
[`_status_event_stream`](webserver/kato_webserver/app.py) emits a synthetic placeholder with `sequence=-1` so the UI never sits on "Connecting…". The broadcaster's real entries always have `sequence >= 1`, so the dedupe set in `useStatusFeed` works fine today. If anyone ever introduces another negative-sequence sentinel (e.g. `-2` for a different bootstrap state), they collide. Cheap to harden: use a UUID or a string sentinel like `'synthetic-open'` instead of `-1`.

### S2.4 — `_pending_publish_lock` is a `threading.Lock`, not `RLock`
If a future caller of `approve_push` re-enters via a callback that also acquires the lock, it deadlocks. Today the lock is only held briefly across `dict.pop`, so this is theoretical. Worth knowing.

---

## Severity 3 — small bugs / edge cases / cleanup

### S3.1 — 10 skipped tests encode dead behavior
`tests/test_agent_service.py`, `tests/test_repository_service.py`, `tests/test_validate_env.py` carry tests marked `@unittest.skip(...)` with reasons like *"Obsolete: validate_connections is now lazy"*. They're load-bearing as a record of intent ("we deliberately removed eager validate"), but they don't run. **Decide:** keep them as documentation, or delete them. I'd lean delete after one release cycle so the suite stays clean.

### S3.2 — `test_kato_core_lib.test_builds_data_access_and_service_in_core_lib` brittle to constructor changes
The test asserts `mock_service_cls.assert_called_once_with(...)` with a long kwarg list. Every time `AgentService.__init__` gains a kwarg, this test breaks until the kwarg is added with `=ANY`. **Fix:** assert only the kwargs the test cares about (e.g. `task_publisher=...`) via `mock_service_cls.call_args.kwargs[key]` lookups. Lower maintenance cost.

### S3.3 — Heartbeat broadcasts may overwhelm SSE buffer on long idle
`_idle_with_heartbeat` logs `Idle · next scan in Xs` every 5 seconds. The broadcaster ring buffer is 500 entries. Over an 8-hour idle window that's ~5,760 heartbeat entries. The UI filters them out of the visible history (correct) but they still consume the buffer, evicting real events that came earlier. **Fix:** drop heartbeats from the broadcaster at the source — log them via a separate channel, or filter them in `StatusBroadcastHandler.emit`.

### S3.4 — `architecture_doc_utils.read_architecture_doc` cap bypasses on the wrapper
The 200k char cap applies to the *body*. The wrapper adds ~1,200 chars on top. So the actual `--append-system-prompt` value is ~201,200 chars. Won't break anything but the cap is misleadingly named.

### S3.5 — Tab `data-task-id` attribute relies on tasks being unique strings
[`Tab.jsx`](webserver/ui/src/components/Tab.jsx) sets `data-task-id={session.task_id}`. If `task_id` were ever empty or duplicate (it shouldn't be), the DOM selector helpers in `useNotifications` (`querySelector(\`[data-task-id="${id}"]\`)`) would silently no-op. Defensive guard worth adding in `useSessions` to reject duplicate-id sessions.

### S3.6 — `formatToolUse` swallows formatter errors silently
```js
try {
  return formatter(input || {});
} catch (_) {
  // Fallback to raw on any formatter error.
}
```
If the formatter throws on unexpected input shapes, the fallback hides the issue. **Fix:** `console.warn(toolName, err)` in the catch so dev tools see it.

---

## Severity 4 — nits / style / documentation drift

### S4.1 — `KATO_MAX_PARALLEL_TASKS` default change not loud enough
Bumped from 1 → 2 this session. Operators upgrading a working install will see slightly different behavior on first boot. The `.env.example` comment explains it but operators with an existing `.env` won't see the change. **Mitigation:** the existing `.env` file overrides the default, so anyone with `KATO_MAX_PARALLEL_TASKS=1` set still gets 1. New installs get 2. Acceptable, but worth a CHANGELOG entry if you keep one.

### S4.2 — `architecture.md` §3 package map shows `webserver/static/build/app.{js,css}` as committed
That's true today (so the git checkout works without npm). If you ever switch to building in CI and not committing the artifact, this line becomes wrong.

### S4.3 — `SETUP.md` reset recipe has no Windows reset for Claude session dir
The "one-shot reset" Windows section wipes `%USERPROFILE%\.kato\workspaces` and `%USERPROFILE%\.kato\sessions` but doesn't mention `%USERPROFILE%\.claude\projects` (Claude's transcript dir). The POSIX reset omits this too. Probably fine — it's Claude-side state, not kato's — but worth mentioning if you want a true "start from scratch."

### S4.4 — `formatToolUse.shortPath` keeps last 2 segments unconditionally
A path like `/very/short` becomes `…/very/short` which is longer than the original. Tiny visual nit. **Fix:** only truncate when the path has more than 2 segments AND the elided portion is longer than the `…/` prefix.

### S4.5 — `Bubble.jsx` exposes a `KIND_LABELS` map but `EventLog.jsx` writes its own labels via the kind enum
Two pieces of code map kinds to display strings. Today they don't conflict (`Bubble.jsx`'s label is "TOOL", `EventLog.jsx` doesn't override it). If the labeling diverges, you end up with `TOOL` in some bubbles and `tool` in others. Not currently an issue.

### S4.6 — `test_repository_connections_validator.py` was rewritten with a richer mock; the rest of the test suites still use a thinner mock pattern
Inconsistency, not a bug. If you adopt the lazy `_repositories=None` pattern across all test fixtures, you remove a class of "Mock not iterable" foot-guns when constructors change.

### S4.7 — `architecture.md` §5.1 references `repository_discovery_utils.py:45-50`
Line numbers in code references rot fast. The actual line of the dir-pruning logic is whatever it is today; future edits will misdirect readers. **Fix:** drop the line range and link to the file only — `[repository_discovery_utils.py](kato/helpers/repository_discovery_utils.py)`. The reader can grep.

---

## What's not reviewed here (out of scope this round)

- **Per-platform issue clients** (YouTrack, Jira, GitHub, GitLab, Bitbucket) — each has its own quirks. They're tested via the boundary suites; deeper review would need the actual platform docs in front of you.
- **OpenHands integration** — kato supports it but the user is on Claude. Behavior tested but not exercised this session.
- **`webserver/ui/build` artifact** — committed and large; out of scope for code review.
- **Real Windows execution** of `bootstrap.py` / `run_local.py` / `clean.py`. Structurally clean (`pathlib`, `os.name == 'nt'` gating); no real-world test happened.
- **i18n / accessibility** — kato UI has zero i18n; some `aria-*` attributes present but no full a11y audit.
- **Bundle size growth** — Font Awesome added ~84 KB raw. If size matters, switch to per-icon imports plus a tree-shaking plugin or use SVG sprites.

---

## Test status at end of session

```
Ran 812 tests in 56s
OK (skipped=10)
```

Zero failing, zero erroring. The 10 skipped are deliberate — see S3.1.

---

## Recommended next round (if you keep going)

1. **Persist `_pending_publish`** so a kato restart doesn't lose pending approvals (S2.1).
2. **Drop heartbeats from the broadcaster's history** so long-idle windows don't evict real events (S3.3).
3. **Persist test fixture pattern**: every Mock that stands in for `RepositoryService` should set `._repositories = None` (S3.5 + S4.6).
4. **Delete the 10 skipped tests** after you're confident no one's looking for them.
5. Real **Windows test** of the bootstrap flow.

Nothing in this list is urgent.
