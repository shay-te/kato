CODE REVIEW — do NOT change behavior, REVIEW the work on this task's branch before the PR opens.

You are reviewing your OWN changes for this task across every repo in the workspace, as if you were a strict senior reviewer who will REJECT the PR on any real issue. Review the full diff of each repo's task branch against its default branch.

Approach this with FRESH EYES: drop every assumption you formed while writing this code — assume nothing is correct until you've confirmed it. Read the diff DEEPLY, LINE BY LINE — every changed line, both sides of each hunk, and the surrounding context it touches. Do NOT skim, do NOT trust your earlier intent; verify what the code actually does now.

Produce a structured report (markdown). For each finding give file:line, severity (BLOCKER / MAJOR / MINOR / NIT), and a one-line fix. Then FIX every BLOCKER and MAJOR in the code (leave MINOR/NIT as a checklist for me).

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

2. SECURITY (hard gate)
   - NO secrets, tokens, API keys, or credential-shaped strings in any committed file.
   - NO compiled/generated artifacts staged: __pycache__/, *.pyc, .pytest_cache, node_modules, build/dist. (These trip GitHub Push Protection / GH013 and block the push — flag and remove any that exist.)
   - Injection (SQL/command/path traversal), auth/permission checks, unsafe deserialization.

3. TESTS
   - Does every new/changed code path have a test that would FAIL without the change?
   - Run the repo's test suite; report pass/fail with the command used. A change with no test, or a test that doesn't exercise the change, is a BLOCKER.
   - No skipped/commented-out tests left behind.

4. CODE QUALITY (per AGENTS.md)
   - No duplicated logic — reuse existing helpers/components; flag copy-paste.
   - No dead/orphaned code (uncalled functions, unused imports/vars).
   - Names match surrounding code; idiom consistent.
   - No debug prints, console.logs, TODO/FIXME, or leftover scratch code.
   - (Frontend) No logic inside JSX; computations live in helpers.

5. COMMENTS (tighten — you tend to over-comment)
   - Comments stay but must be SHORT: one or two clear lines a human OR an LLM can grasp at a glance.
   - State WHY (rationale, gotcha, invariant) — never narrate WHAT the code already says.
   - DELETE storytelling / changelog / "previously this was…" / step-by-step narration / filler.
   - DELETE obvious comments that restate the next line.
   - Collapse any multi-paragraph comment block down to its essential point.

6. CROSS-REPO CONSISTENCY
   - If a shared contract changed (signature, schema, constant), every repo that depends on it is updated consistently. List the repos touched and confirm they agree.

7. SCOPE
   - Every change is in-scope for this task. Flag anything unrelated.
   - All edits are inside the task folder (no out-of-sandbox writes).

End with a PR-readiness verdict (READY / NOT READY) and, if NOT READY, the exact remaining blockers. Be concise — no praise, only findings.
