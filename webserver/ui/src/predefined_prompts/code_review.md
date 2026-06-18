CODE REVIEW — do NOT change behavior, REVIEW the work on this task's branch before the PR opens.

You are reviewing your OWN changes for this task across every repo in the workspace, as if you were a strict senior reviewer who will REJECT the PR on any real issue. Review the full diff of each repo's task branch against its default branch.

Approach this with FRESH EYES: drop every assumption you formed while writing this code — assume nothing is correct until you've confirmed it. Read the diff DEEPLY, LINE BY LINE — every changed line, both sides of each hunk, and the surrounding context it touches. Do NOT skim, do NOT trust your earlier intent; verify what the code actually does now.

REVIEW THE SYSTEM, NOT THE DIFF. The diff is a clue, not the boundary — most nasty bugs live in `caller → changed function → downstream consumer`, not in the changed lines themselves. For every changed line: (1) find all callers, (2) find all downstream consumers, (3) trace the complete data flow end to end, (4) verify the system's invariants still hold. You are reviewing BEHAVIOR, not lines.

ASSUME THE TESTS ARE WRONG. Passing tests prove only that the tests passed — NOT that the code is correct. Never use green tests as evidence of correctness; verify the behavior yourself from the code.

SYSTEM INVARIANTS. Before reviewing, name the system's core invariants that this change touches (e.g. no data loss, exactly-once / no-duplicate processing, task/tenant isolation, cache consistency, authorization boundaries, "a forgotten task stays forgotten", "no out-of-sandbox writes"). For every change, verify each still holds. A violated invariant is a BLOCKER.

PROJECT RULES. This change MUST comply with the repo's standing docs — `AGENTS.md` (engineering rules / no-redundancy), `architecture.md` (layering, package map, what lives where), and `lessons.md` (mistakes not to repeat). Read all three first, then verify the diff against them; any change that violates one is a BLOCKER (cite the doc + the rule).

Produce a structured report (markdown). For each finding give file:line, severity (BLOCKER / MAJOR / MINOR / NIT), and a one-line fix. Then FIX every BLOCKER and MAJOR in the code (leave MINOR/NIT as a checklist for me).

EVIDENCE, NOT SPECULATION. Every BLOCKER and MAJOR must show: the exact code path, the exact triggering scenario (concrete input/sequence), why the existing tests did not catch it, and a caller/callee trace that proves it. If you cannot demonstrate a concrete failure, DOWNGRADE the finding — no "this might/could break" without proof.

1. BUGS & CORRECTNESS (primary goal — actively HUNT for bugs, don't just skim)
   - Logic errors, off-by-one, inverted/wrong conditionals, operator-precedence slips.
   - Unhandled None/null/undefined, empty collections, missing keys, mutable default args.
   - Boundary/edge cases: zero, negative, very large, empty, unicode, duplicate, out-of-order inputs.
   - Error handling: swallowed exceptions, wrong/over-broad catch, partial failure leaving bad state.
   - Concurrency/races, ordering assumptions, shared-state mutation, re-entrancy, await/async gaps.
   - Resource leaks (files, handles, connections, subprocesses), unclosed contexts, unbounded growth.
   - Off-contract returns: a function returns a shape callers don't expect (None vs [], dict vs bool).
   - Regressions: find every call site of changed signatures/return shapes and verify each still holds.
   - For each suspected bug, give the CONCRETE input/scenario that triggers it and the wrong result.
   - FREE HANDS: this list is a starting point, NOT a limit. Nobody knows what bugs are in
     here — so hunt openly. Follow anything that smells off, trace the data flow end to end,
     question every assumption, and chase your suspicions wherever they lead — into callers,
     callees, configs, tests, and adjacent code the diff touches. If something feels wrong but
     fits no category above, report it anyway. Use your full judgment; surprise me.

2. SECURITY (hard gate — assume every input is HOSTILE)
   - Trace every user-/agent-controlled value from entrypoint to sink. Injection of every kind
     (SQL / command / path traversal / prompt injection / SSRF / XSS), unsafe deserialization,
     auth & permission checks, privilege escalation, tenant / cross-task isolation. Follow tainted
     data end-to-end — don't stop at the first boundary.
   - NO secrets, tokens, API keys, or credential-shaped strings in any committed file.
   - NO compiled/generated artifacts staged: __pycache__/, *.pyc, .pytest_cache, node_modules, build/dist. (These trip GitHub Push Protection / GH013 and block the push — flag and remove any that exist.)

3. TESTS
   - WRITE a test for ALL new code you wrote. Every new/changed function, branch, and edge case
     gets a test that would FAIL without the change. A missing test is not just flagged — ADD it
     now (in the repo's existing test style/location), then run it.
   - A change with no test, or a test that doesn't exercise the change, is a BLOCKER until you've
     written one.
   - REVIEW THE TESTS AS PRODUCTION CODE: a test that can't fail when the feature breaks is no
     test. Hunt for assertions that don't verify the actual behavior, mocks that hide the bug,
     tests coupled to implementation details, and tests that pass for the wrong reason — flip the
     code to confirm the test would actually go red.
   - Run the repo's test suite; report pass/fail with the command used.
   - No skipped/commented-out tests left behind.

4. CODE QUALITY (per AGENTS.md)
   - No duplicated logic — reuse existing helpers/components; flag copy-paste.
   - No magic numbers — extract them to named constants (literals are OK in tests only).
   - No dead/orphaned code (uncalled functions, unused imports/vars).
   - Hunt DEEPLY for dead & redundant code, not just the diff: trace each new/changed symbol's callers across the whole repo, and DELETE anything now-unreferenced — orphaned helpers, superseded code paths, near-duplicate implementations (consolidate into one), and their tests. "It still passes" is not enough; if it's unreachable or duplicated, remove it.
   - For every NEW implementation, actively ask: did it make an OLD one obsolete? is there now a duplicate path, dead config, or stale test? Prefer DELETION over addition.
   - Names match surrounding code; idiom consistent.
   - No debug prints, console.logs, TODO/FIXME, or leftover scratch code.
   - (Frontend) No logic inside JSX; computations live in helpers.

5. COMMENTS (tighten — you tend to over-comment)
   - Comments stay but must be SHORT: one or two clear lines a human OR an LLM can grasp at a glance.
   - State WHY (rationale, gotcha, invariant) — never narrate WHAT the code already says.
   - DELETE storytelling / changelog / "previously this was…" / step-by-step narration / filler.
   - DELETE obvious comments that restate the next line.
   - Collapse any multi-paragraph comment block down to its essential point.

6. ARCHITECTURE & DESIGN (assume this code still exists in 3 years)
   - Designs that technically work but raise future complexity; violations of the repo's existing patterns.
   - Hidden coupling between modules; abstractions that leak implementation details.
   - Business logic that migrated into infra layers, or infra concerns that leaked into domain logic.
   - Circular dependencies; changes that widen the blast radius of a failure.
   - For each: name the future failure mode, why it's risky, and the SMALLEST safer design.

7. PRODUCTION FAILURE MODES (most reviewers skip this — don't)
   - For every changed path, ask "what happens if this fails in production?" Simulate: network
     timeout, process restart mid-operation, duplicate event, out-of-order event, message replay,
     stale cache, partial write, downstream/service unavailable, clock skew, concurrent requests.
   - Trace system state before and after. Report any path that can corrupt state, lose data,
     duplicate data, deadlock, retry forever, or leak resources. Give the concrete sequence.

8. PERFORMANCE & OPERATIONAL COST (if the path is hot — skip for clearly cold/one-shot code)
   - Assume the path runs at high volume. Algorithmic complexity, N+1 queries, repeated
     serialize/deserialize or JSON parsing, redundant network / DB calls, lock contention, hot
     loops, needless allocations. For each finding: current complexity → improved → expected impact.
   - Estimate the cost delta: DB/cache load, memory, network traffic, queue depth, storage growth.
     Flag any UNBOUNDED growth (a list/map/file/log that only ever grows). If complexity increased,
     justify why.

9. CONTRACTS & COMPATIBILITY (if a shared shape changed)
   - Every changed API response / DTO / schema / event / message / DB record / tool response:
     check backward AND forward compatibility — nullability changes, enum changes, field
     add/remove, default values. Trace EVERY producer and consumer and confirm they still agree.

10. DATABASE SAFETY (if there are migrations / queries)
   - Indexes, migrations, locking, transactions, isolation levels. Look for full table scans,
     missing indexes, long-running transactions, races. A migration that cannot safely run (or
     roll back) against a live production DB is a BLOCKER.

11. OBSERVABILITY (if it breaks at 3am, can an engineer diagnose it?)
   - Silent failures, swallowed exceptions, missing context in logs/errors, impossible-to-debug
     code paths. Flag where a failure would be invisible or untraceable.

12. CROSS-REPO CONSISTENCY
   - If a shared contract changed (signature, schema, constant), every repo that depends on it is updated consistently. List the repos touched and confirm they agree.

13. SCOPE
   - Every change is in-scope for this task. Flag anything unrelated.
   - All edits are inside the task folder (no out-of-sandbox writes).

14. ATTACK YOUR OWN CHANGE (the capstone — this catches the worst bugs)
   - Assume the author (you) is WRONG. Spend at least as much effort trying to DISPROVE correctness
     as confirming it. For each change ask: what assumption is false? what input breaks this? what
     happens at 10x scale? after 6 months of data growth? what would wake me at 3am? Do NOT stop at
     the first issue — keep going until no plausible failure mode remains. The best reviewers don't
     verify correctness; they try to break it.

BEFORE YOU REPORT — RUN ALL THE TESTS (mandatory, do this last)
- After fixing the BLOCKERs/MAJORs, RUN THE FULL TEST SUITE of every repo you touched
  (not just the files you changed) — the project's normal test command for each.
- If any test fails, FIX it and re-run until green, or list the failure as a BLOCKER if
  you can't. Do NOT report "READY" while any test is red.
- The report MUST include the test results: per repo, the exact command run and the
  pass/fail counts (e.g. "core-lib: 312 passed, 0 failed"). No tests run = NOT READY.

OUTPUT FORMAT
- Open with a SUMMARY TABLE — one row per file that has findings, columns:
  | File | Blocker | Major | Minor | Nit | Total |
  Sort by Total descending (worst files first); add a TOTALS row at the bottom.
- Then list the findings themselves, PRIORITIZED top to bottom by severity:
  all BLOCKERs first, then MAJORs, then MINORs, then NITs — most important issues
  at the top so I can act on them in order. Within a severity, order by impact.
- Then a TEST RESULTS section: one line per repo with the command + pass/fail counts.

End with a PR-readiness verdict (READY / NOT READY) and, if NOT READY, the exact remaining blockers. Be concise — no praise, only findings.
