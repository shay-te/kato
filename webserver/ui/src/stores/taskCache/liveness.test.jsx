// The seam between agentStatusStore and the task-cache poll cadence.
//
// Every createTaskCache test injects a FAKE isTaskLive, so the composition the
// production store actually wires up —
//
//     isTaskLive: (taskId) => isAgentActive(agentStatusStore.getStatus(taskId))
//
// (stores/taskCache/index.js) — was never executed by anything. That is where
// the real defect lived: the predicate reads the CLIENT's view of the session,
// which can be wrong in one direction, and the cache tests could not see it
// because they never used the real one.
//
// These drive the real store through the real predicate.

import { describe, test, expect, beforeEach } from 'vitest';

import { agentStatusStore } from '../agentStatusStore.js';
import { isAgentActive } from '../../utils/agentStatus.js';
import { SESSION_LIFECYCLE } from '../../hooks/useSessionStream.js';

// Verbatim the wiring in stores/taskCache/index.js.
const isTaskLive = (taskId) => isAgentActive(agentStatusStore.getStatus(taskId));

beforeEach(() => { agentStatusStore.clearAll(); });

describe('task-cache liveness — the real agentStatusStore composition', () => {
  test('a task nobody has reported on polls at the FAST cadence', () => {
    // First paint, and any task whose SessionDetail has not published yet.
    // Under-polling a busy task shows stale code; over-polling an idle one
    // costs only what the backoff was trimming.
    expect(isTaskLive('T-never-seen')).toBe(true);
  });

  test('a working task is live', () => {
    agentStatusStore.setStatus('T1', {
      lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true,
    });
    expect(isTaskLive('T1')).toBe(true);
  });

  test('a connected-but-quiet task BACKS OFF — the dominant steady state', () => {
    // A live subprocess with no turn running sits in STREAMING for as long as
    // the operator reads a diff. Treating that as live meant the backoff never
    // engaged where it mattered most, and the ~150 git subprocesses a minute
    // it exists to remove were still being spent.
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.STREAMING });
    expect(isTaskLive('T1')).toBe(false);
  });

  test('a task waiting on a background job is live', () => {
    agentStatusStore.setStatus('T1', {
      lifecycle: SESSION_LIFECYCLE.STREAMING, awaitingBackground: true,
    });
    expect(isTaskLive('T1')).toBe(true);
  });

  test('a SLEEPING task backs off too', () => {
    // No subprocess at all, and the stream reconnects on a <=30s backoff, so a
    // subprocess appearing server-side is noticed.
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.IDLE });
    expect(isTaskLive('T1')).toBe(false);
  });

  test('a CLOSED stream does NOT back off', () => {
    // useSessionStream's retry effect early-returns for any lifecycle but
    // IDLE, so a closed stream is never reopened and this view can never
    // correct itself. kato's 180s scan or a draining comment can spawn a
    // subprocess the client will never hear about — so backing off here would
    // leave the panes slow with nothing to ever speed them up again.
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.CLOSED });
    expect(isTaskLive('T1')).toBe(true);
  });

  test('a MISSING record does NOT back off', () => {
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.MISSING });
    expect(isTaskLive('T1')).toBe(true);
  });

  test('clearing a task on unmount returns it to the fast cadence', () => {
    // SessionDetail clears its entry on unmount, so the very next poll tick
    // sees nothing published. That must read as "unknown → fast", not as
    // "quiet".
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.IDLE });
    expect(isTaskLive('T1')).toBe(false);

    agentStatusStore.clearStatus('T1');
    expect(isTaskLive('T1')).toBe(true);
  });

  test('one task going quiet does not slow another', () => {
    agentStatusStore.setStatus('T1', { lifecycle: SESSION_LIFECYCLE.IDLE });
    agentStatusStore.setStatus('T2', {
      lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true,
    });
    expect(isTaskLive('T1')).toBe(false);
    expect(isTaskLive('T2')).toBe(true);
  });
});
