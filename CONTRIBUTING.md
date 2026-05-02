# Contributing to kato

Thanks for thinking about contributing. Kato is a small, opinionated project; the contribution process is designed to keep it that way.

## Before you open a PR

1. **Read [AGENTS.md](AGENTS.md).** It is the authoritative coding-rules document for this repo (used by both human contributors and by kato when it works on its own codebase). Architecture, naming, test discipline, error reporting, comment style — all there.
2. **Read [architecture.md](architecture.md).** Section 2 spells out the `core-lib` principles the codebase enforces. New code that doesn't fit the layers (`client/`, `data_layers/data/`, `data_layers/data_access/`, `data_layers/service/`, `jobs/`, `helpers/`) is almost always in the wrong place.
3. **Read [SECURITY.md](SECURITY.md)** if your change touches anything security-shaped: tool permissions, agent invocation, credentials, the planning UI, the bypass-permissions flag.
4. **Run the test suite locally**: `make test` (POSIX) or `python -m unittest discover -s tests` (any OS). New behavior gets a test; bug fixes get a regression test. See AGENTS.md → Testing.

## Pull-request shape

- **Small, direct changes.** A bug fix that touches one function is better than a fix bundled with three refactors. Keep the diff focused.
- **One concern per PR.** If you find an unrelated bug while working on something else, open a separate PR for it.
- **Commits with clear messages.** No `WIP`, no `fix stuff`, no `address review`. The reader of `git log --oneline` should be able to reconstruct what each commit changed without opening it.
- **PR description must explain *why*.** The diff explains *what*. The description explains why the change is needed and what alternatives were considered.

## What we won't merge

- Backwards-compatibility shims for code paths that don't have callers.
- New abstractions for hypothetical future requirements.
- Comments that explain *what* the code does (well-named identifiers handle that). Comments that mention the current PR / task / issue (those rot; PR descriptions don't).
- Code that bypasses the layered structure (services calling clients directly without going through data-access, etc.).
- Test bootstrap shims, `sys.modules` injection, or fake-package facades. Tests run against the real installed packages.
- Mocks of `core-lib` itself or its base classes. Mock the *external* boundary (HTTP, subprocess, filesystem); use real `Service` / `DataAccess` subclasses.
- Changes to the bypass-permissions flag, the planning UI security banner, or the git denylist without a SECURITY.md update.
- Code that gives Claude or any other agent the ability to run `git`. Kato is the only component that runs git operations; this is enforced at the tool-permission layer and at the prompt layer. See architecture.md §5.10.

## Reporting security issues

Please don't open a public issue for security-shaped reports. Use the private disclosure path in [SECURITY.md](SECURITY.md).

## Code style

See AGENTS.md → "Code Style". Three things worth repeating:

- Class declarations always state a base: `class Foo(object):` instead of `class Foo:`. Matches the rest of the codebase.
- Snake-case filenames, CamelCase class names. The file and its primary class share a name (`StartupDependencyValidator` lives in `startup_dependency_validator.py`).
- Constants from `kato/data_layers/data/fields.py` are preferred over free-text field names.

## Recovering from git mistakes

One-paragraph entries per genuine incident — keep this section practical, not "Postmortems."

### Discarding uncommitted work via `git checkout --` (2026-05-02)

Mid-sweep on the bypass-mode hardening, ran `git checkout -- BYPASS_PROTECTIONS.md kato/sandbox/manager.py` to revert what looked like a couple of small hunks before splitting commits. Both files held hours of uncommitted hardening work; that work was destroyed. `git checkout -- <file>` overwrites the working tree with the committed version — it does not move HEAD, is not in the reflog, and is not undoable from there.

**Lesson:** capture before revert. Use `git stash` (which retains a reachable ref in `refs/stash` and can be popped) over `git checkout --` whenever the working tree contains anything uncommitted. After any recovery from a mistake of this shape, verify intent-equivalence — run the test suite and mutate the recovered code to confirm assertions still fire — before claiming the loss was clean.

**Recovery technique that worked:** `git fsck --lost-found` surfaced a dangling stash commit (the editor's auto-stash from earlier in the session) containing most of the lost work. Remaining deltas were re-applied from `/tmp` snapshots captured before the destructive command. When you are about to do anything irreversible, capture those snapshots proactively — `cp file /tmp/file.bak` is cheap insurance.

## What kato is, what kato isn't

Kato is an unattended agent orchestrator. Its job is to scan a ticket platform, run an agent backend on each task, and publish the result as a PR. It is *not* a sandbox, a code-review tool, or a CI system. Contributions that try to expand its scope into one of those will be redirected.

Thanks for reading this far. Open the PR.
