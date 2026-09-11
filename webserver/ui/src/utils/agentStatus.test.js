import assert from 'node:assert/strict';
import test from 'node:test';

import { deriveAgentStatus, badgeKindFor, isAgentActive } from './agentStatus.js';
import { AGENT_STATUS_KIND } from '../constants/agentStatusKind.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';

// A plain "active workspace, live subprocess" session. Individual tests tweak
// fields. ``status: 'active'`` keeps the workspace axis out of the way so we
// isolate the agent-liveness axis.
function session(extra = {}) {
  return { task_id: 'T1', status: 'active', live: true, working: false, ...extra };
}

function live(extra = {}) {
  return { lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false, ...extra };
}

// ---- Active (live SSE) path: one case per kind -----------------------------

test('active path maps each lifecycle to the right kind/label', () => {
  const cases = [
    [SESSION_LIFECYCLE.STREAMING, 'idle', 'idle'],
    [SESSION_LIFECYCLE.CONNECTING, 'connecting', 'connecting'],
    [SESSION_LIFECYCLE.IDLE, 'sleeping', 'sleeping'],
    [SESSION_LIFECYCLE.CLOSED, 'closed', 'closed'],
    [SESSION_LIFECYCLE.MISSING, 'missing', 'no record'],
  ];
  for (const [lifecycle, kind, label] of cases) {
    const got = deriveAgentStatus(session(), live({ lifecycle }), false);
    assert.equal(got.kind, kind, `${lifecycle} → kind`);
    assert.equal(got.label, label, `${lifecycle} → label`);
  }
});

test('UNA-2492 regression: live lifecycle=closed wins over a stale polled working=true', () => {
  // The exact bug: the chip (live) said closed, the tab (polled) said working.
  // With the unified derivation the live state wins → everyone shows closed.
  const got = deriveAgentStatus(
    session({ working: true, live: true }), // stale poll says working
    live({ lifecycle: SESSION_LIFECYCLE.CLOSED, turnInFlight: false }),
    false,
  );
  assert.equal(got.kind, 'closed');
  assert.equal(got.label, 'closed');
});

test('active precedence: turnInFlight beats lifecycle (working), provisioning beats all', () => {
  const working = deriveAgentStatus(
    session(), live({ lifecycle: SESSION_LIFECYCLE.CLOSED, turnInFlight: true }), false,
  );
  assert.equal(working.kind, 'working');

  const provisioning = deriveAgentStatus(
    session({ status: 'provisioning' }),
    live({ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true }),
    true,
  );
  assert.equal(provisioning.kind, 'provisioning');
});

test('active: needsAttention → approval (but turnInFlight still wins)', () => {
  const approval = deriveAgentStatus(session(), live({ lifecycle: SESSION_LIFECYCLE.STREAMING }), true);
  assert.equal(approval.kind, 'approval');

  const working = deriveAgentStatus(session(), live({ turnInFlight: true }), true);
  assert.equal(working.kind, 'working');
});

// ---- Polled fallback path (no live status) ---------------------------------

test('polled fallback maps each field combo to the right kind (matches old claudeBadge)', () => {
  assert.equal(deriveAgentStatus(session({ working: true }), null, false).kind, 'working');
  assert.equal(deriveAgentStatus(session({ has_pending_permission: true }), null, false).kind, 'approval');
  assert.equal(deriveAgentStatus(session({ live: false }), null, false).kind, 'sleeping');
  assert.equal(deriveAgentStatus(session({ live: true }), null, false).kind, 'idle');
  assert.equal(deriveAgentStatus(session({ status: 'provisioning' }), null, false).kind, 'provisioning');
});

test('polled fallback: non-live tab reads closed when terminal, else sleeping', () => {
  // The real pollable distinction for background tabs (no live stream): a
  // finished/stopped task is closed; any other non-live tab will lazily
  // respawn on the next message → sleeping. Fixes done tabs showing sleeping.
  assert.equal(deriveAgentStatus(session({ live: false, status: 'done' }), null, false).kind, 'closed');
  assert.equal(deriveAgentStatus(session({ live: false, status: 'terminated' }), null, false).kind, 'closed');
  assert.equal(deriveAgentStatus(session({ live: false, status: 'errored' }), null, false).kind, 'closed');
  assert.equal(deriveAgentStatus(session({ live: false, status: 'active' }), null, false).kind, 'sleeping');
  assert.equal(deriveAgentStatus(session({ live: false, status: 'review' }), null, false).kind, 'sleeping');
});

// ---- dotClass follows the same agent kind as the chip -----------------------

test('dotClass keeps the workspace status (review/done) and attention override', () => {
  const review = deriveAgentStatus(session({ status: 'review' }), null, false);
  assert.match(review.dotClass, /status-review/);
  assert.equal(review.status, 'review');

  const attention = deriveAgentStatus(session({ status: 'review' }), null, true);
  assert.match(attention.dotClass, /status-attention/);
});

test('dotClass trusts live working kind even when the workspace status is review', () => {
  const got = deriveAgentStatus(
    session({ status: 'review', working: false }),
    live({ turnInFlight: true }),
    false,
  );

  assert.equal(got.kind, 'working');
  assert.match(got.dotClass, /status-working/);
  assert.equal(got.status, 'working');
});

test('dotClass marks provisioning as loading', () => {
  const got = deriveAgentStatus(session({ status: 'provisioning' }), null, false);
  assert.match(got.dotClass, /is-loading/);
});

// ---- badgeKindFor mapping ---------------------------------------------------

test('badgeKindFor maps kinds to the existing tooltip badge classes', () => {
  assert.equal(badgeKindFor('working'), 'work');
  assert.equal(badgeKindFor('idle'), 'idle');
  assert.equal(badgeKindFor('connecting'), 'idle');
  assert.equal(badgeKindFor('sleeping'), 'sleep');
  assert.equal(badgeKindFor('closed'), 'sleep');
  assert.equal(badgeKindFor('approval'), 'wait');
  // no badge styling → '' (caller renders no badge)
  assert.equal(badgeKindFor('provisioning'), '');
  assert.equal(badgeKindFor('missing'), '');
  assert.equal(badgeKindFor('unknown'), '');
});

// ---- awaitingBackground: busy, but NOT the same thing as a live turn -------

test('awaitingBackground (turn closed, blocked on a Monitor wait) → background', () => {
  // NOT "working". The turn has closed, so the in-chat working animation —
  // which reads ``turnInFlight`` — is correctly absent; reporting WORKING here
  // put a "working" chip and dot next to nothing working, on a task the
  // operator had already finished and merged. Its own kind keeps the two
  // surfaces telling the same story.
  const got = deriveAgentStatus(
    session(),
    // turn closed (turnInFlight false) but waiting on a background task
    live({ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false, awaitingBackground: true }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.BACKGROUND);
  assert.equal(got.label, 'background');
  // Still visibly busy — it shares the workflow dot, not the working one.
  assert.equal(got.status, 'workflow');
  assert.equal(badgeKindFor(got.kind), 'flow');
});

test('no awaitingBackground + closed turn → idle (unchanged)', () => {
  const got = deriveAgentStatus(
    session(),
    live({ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.IDLE);
});

// ---- background WORKFLOW: its own status + colour --------------------------

test('background WORKFLOW (turn closed, workflow still running) → workflow, indigo dot', () => {
  const got = deriveAgentStatus(
    session(),
    live({
      lifecycle: SESSION_LIFECYCLE.STREAMING,
      turnInFlight: false,
      awaitingBackground: true,
      backgroundIsWorkflow: true,
    }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.WORKFLOW);
  assert.equal(got.label, 'workflow');
  // Distinct dot from working — its own status class so the CSS paints indigo.
  assert.equal(got.status, 'workflow');
  assert.ok(got.dotClass.includes('status-workflow'));
  assert.equal(badgeKindFor(got.kind), 'flow');
});

test('an IN-FLIGHT turn stays "working" even if the turn also has a workflow', () => {
  // While the foreground turn is live, working wins — the workflow status is
  // for the AFTER-the-turn background window.
  const got = deriveAgentStatus(
    session(),
    live({ turnInFlight: true, awaitingBackground: false, backgroundIsWorkflow: true }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.WORKING);
});

test('a non-workflow background wait (Monitor) reads background, not workflow', () => {
  const got = deriveAgentStatus(
    session(),
    live({ turnInFlight: false, awaitingBackground: true, backgroundIsWorkflow: false }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.BACKGROUND);
});

test('the polled working flag cannot override the live store', () => {
  // The regression this whole split exists for: a task whose agent has
  // finished (live store: streaming, no turn, no background wait) while the
  // 5s-polled ``working`` flag is still true. Every surface must read the
  // live answer — chip, label AND dot — because they all come from here.
  const got = deriveAgentStatus(
    { ...session(), working: true },
    live({ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: false }),
    false,
  );
  assert.equal(got.kind, AGENT_STATUS_KIND.IDLE);
  assert.equal(got.label, 'idle');
  assert.ok(!got.dotClass.includes('status-working'));
});


// The task-cache poller picks its cadence from this: chase a working agent at
// 5s, back off to 30s for a sleeping one. Derived here, next to
// deriveAgentStatus, so agent liveness keeps exactly one definition.

test('isAgentActive: a turn in flight is active', () => {
  assert.equal(
    isAgentActive({ lifecycle: SESSION_LIFECYCLE.IDLE, turnInFlight: true }), true,
  );
});

test('isAgentActive: a background wait the turn scheduled is still active', () => {
  // Monitor / Workflow / run_in_background — the turn has closed but edits can
  // still land, so the panes must keep chasing it.
  assert.equal(
    isAgentActive({ lifecycle: SESSION_LIFECYCLE.IDLE, awaitingBackground: true }), true,
  );
});

test('isAgentActive: a connected session with NO turn is quiet', () => {
  // STREAMING means the stream is OPEN — this module maps it to the "idle"
  // chip. It is where a new turn flips turnInFlight within one frame, so it is
  // the SAFEST state to slow down in, not the least safe. Calling it active
  // made the backoff inert in the state a task sits in for as long as the
  // operator reads a diff.
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.STREAMING }), false);
});

test('isAgentActive: a connecting session is active', () => {
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.CONNECTING }), true);
});

test('isAgentActive: a sleeping session is not', () => {
  // IDLE means no live subprocess — it cannot be editing anything.
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.IDLE }), false);
});

test('isAgentActive: a CLOSED or MISSING session counts as ACTIVE', () => {
  // Not an oversight — the narrow case is deliberate. useSessionStream's retry
  // effect early-returns for any lifecycle but IDLE, so a closed stream is
  // never reopened and this client-side view can never correct itself. kato's
  // scan loop or a draining comment can spawn a subprocess the client will
  // never hear about, so a wrong "quiet" here would never be revisited.
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.CLOSED }), true);
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.MISSING }), true);
});

test('isAgentActive: quiet is about how fast the client would find out', () => {
  // STREAMING: open stream, immediate. IDLE: no subprocess, and the stream
  // reconnects on a <=30s backoff. Anything else — including a lifecycle this
  // build has never heard of — is active, because it cannot be shown to
  // correct itself.
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.STREAMING }), false);
  assert.equal(isAgentActive({ lifecycle: SESSION_LIFECYCLE.IDLE }), false);
  assert.equal(isAgentActive({ lifecycle: 'something-new' }), true);
});

test('isAgentActive: a turn on an OPEN stream is still active', () => {
  // The quiet cases are gated on there being no work — not on the lifecycle
  // alone.
  assert.equal(isAgentActive({
    lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true,
  }), true);
  assert.equal(isAgentActive({
    lifecycle: SESSION_LIFECYCLE.STREAMING, awaitingBackground: true,
  }), true);
});

test('isAgentActive: unknown status counts as ACTIVE', () => {
  // First paint, before SessionDetail publishes. Under-polling a busy task
  // shows the operator stale code; over-polling an idle one only costs the
  // thing we are trimming — so the unknown case fails toward correctness.
  assert.equal(isAgentActive(null), true);
  assert.equal(isAgentActive(undefined), true);
});

// ---- The focused/unfocused disagreement ------------------------------------
//
// Reported three times, most recently as: "the task tab show green, moving to
// it he become yellow working. it's not reflecting the reality."
//
// Cause: ``system/init`` set ``turnInFlight``. That event fires on EVERY
// spawn, including the one kato does simply because the operator opened the
// task (lazy resume). No prompt follows it, so no ``result`` ever arrived to
// clear the flag — the tab was green in the strip and yellow the moment you
// looked at it, on a task where nothing was running.

test('opening an idle task does not turn it "working"', () => {
  // The live stream has connected and nothing is running: the server agrees.
  const status = deriveAgentStatus(session({ working: false }), live(), false, 'Claude');
  assert.equal(status.kind, AGENT_STATUS_KIND.IDLE);
});

test('the focused and unfocused views agree on an idle task', () => {
  // The whole point: same session, one derivation, same answer whether or not
  // the live store has an entry for it.
  const idle = session({ working: false });
  const focused = deriveAgentStatus(idle, live(), false, 'Claude');
  const background = deriveAgentStatus(idle, null, false, 'Claude');
  assert.equal(focused.kind, background.kind);
  assert.equal(focused.label, background.label);
});

test('the focused and unfocused views agree on a WORKING task', () => {
  const busy = session({ working: true });
  const focused = deriveAgentStatus(busy, live({ turnInFlight: true }), false, 'Claude');
  const background = deriveAgentStatus(busy, null, false, 'Claude');
  assert.equal(focused.kind, AGENT_STATUS_KIND.WORKING);
  assert.equal(background.kind, AGENT_STATUS_KIND.WORKING);
});

test('a just-sent message shows working before the server catches up', () => {
  // ``markTurnBusy`` fires on send, seconds before the 5s poll would report
  // it. Only the live stream knows this — which is why the live stream, not
  // the poll, is the authority for a focused tab.
  const status = deriveAgentStatus(
    session({ working: false }), live({ turnInFlight: true }), false, 'Claude',
  );
  assert.equal(status.kind, AGENT_STATUS_KIND.WORKING);
});

test('the dot and the label never disagree on one tab', () => {
  // They used to be computed from different facts.
  for (const [live_, working] of [[true, false], [false, true], [false, false]]) {
    const status = deriveAgentStatus(
      session({ working }), live({ turnInFlight: live_ }), false, 'Claude',
    );
    const isWorking = status.kind === AGENT_STATUS_KIND.WORKING;
    // ``idle-alive`` is the dot's way of saying "connected, doing nothing".
    assert.equal(
      /idle-alive/.test(status.dotClass), !isWorking,
      `dot and label disagree for live=${live_} working=${working}`,
    );
  }
});

