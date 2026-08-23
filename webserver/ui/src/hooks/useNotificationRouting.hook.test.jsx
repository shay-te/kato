// Tests for useNotificationRouting — translates SSE events into
// notify() calls with the right kind, title, body, taskId. This
// is the bridge between the status feed / session stream and the
// notification surface; a bug here silently drops notifications.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({ fetchPendingPermissions: vi.fn() }));

import { fetchPendingPermissions } from '../api.js';

import { useNotificationRouting } from './useNotificationRouting.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';

// Never sleep for real: the retry below is a 1.5s wall-clock wait.
const NO_WAIT = { wait: () => Promise.resolve() };

function _pending(taskId) {
  return { pending: [{ task_id: taskId, request_id: 'r1', request: { tool_name: 'WebFetch' } }] };
}

// The status line's ping is decided in a promise chain, so a negative
// assertion has to wait for that chain to settle — not just for the
// synchronous call to return.
async function _settled(calls = 2) {
  await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalledTimes(calls));
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  fetchPendingPermissions.mockReset();
  fetchPendingPermissions.mockResolvedValue({ pending: [] });
});


describe('useNotificationRouting — onStatusEntry', () => {

  test('classifies a recognised status entry and forwards to notify', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onStatusEntry({
      message: 'Mission PROJ-1: starting mission: fix the login bug',
    });

    expect(notify).toHaveBeenCalledTimes(1);
    const arg = notify.mock.calls[0][0];
    expect(arg.kind).toBe(NOTIFICATION_KIND.STARTED);
    expect(arg.taskId).toBe('PROJ-1');
  });

  test('unrecognised status entry is dropped silently', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));
    result.current.onStatusEntry({ message: 'something random' });
    expect(notify).not.toHaveBeenCalled();
  });

  test('null / undefined entries do not crash', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));
    result.current.onStatusEntry(null);
    result.current.onStatusEntry(undefined);
    expect(notify).not.toHaveBeenCalled();
  });

  test('status-feed permission ask for the FOCUSED task is SUPPRESSED', () => {
    // onSessionEvent already pings the focused task off the live SSE
    // stream (with command-level recall). The orchestrator status feed
    // emits a duplicate "asking permission" line for the same ask; firing
    // off it too would double-ping — and worse, it carries only the tool
    // name so it can't see a remembered (Bash, mvn) grant.
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'PROJ-8',
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run Bash',
    });

    expect(notify).not.toHaveBeenCalled();
  });

  test('a BACKGROUND ask the backend already auto-approved does NOT ping', async () => {
    // The operator-reported leak: "I get a browser notification, switch to
    // the kato tab, and it was already approved." The status line is
    // emitted BEFORE the webserver's auto-resolve runs, so it fires for
    // asks that never need a human. /api/permissions/pending lists only
    // what does — and it lists nothing here.
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'OTHER', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    await _settled();
    expect(notify).not.toHaveBeenCalled();
  });

  test('a BACKGROUND ask still waiting on a human DOES ping', async () => {
    const notify = vi.fn();
    fetchPendingPermissions.mockResolvedValue(_pending('PROJ-8'));
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'OTHER', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
    expect(notify.mock.calls[0][0].taskId).toBe('PROJ-8');
    // One look was enough — no retry needed.
    expect(fetchPendingPermissions).toHaveBeenCalledTimes(1);
  });

  test('an ask not registered yet is caught by the second look', async () => {
    // The status line can land before the session records the ask.
    const notify = vi.fn();
    fetchPendingPermissions
      .mockResolvedValueOnce({ pending: [] })
      .mockResolvedValueOnce(_pending('PROJ-8'));
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'OTHER', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
  });

  test('a pending ask on a DIFFERENT task does not ping for this one', async () => {
    const notify = vi.fn();
    fetchPendingPermissions.mockResolvedValue(_pending('SOMEONE-ELSE'));
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'OTHER', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    await _settled();
    expect(notify).not.toHaveBeenCalled();
  });

  test('an unreachable backend pings anyway', async () => {
    // Fail LOUD: a false ping costs a glance, a missed one costs an agent
    // every minute it sits blocked with nobody watching.
    const notify = vi.fn();
    fetchPendingPermissions.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'OTHER', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
  });

  test('the focused task never even asks the backend', async () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'PROJ-8', ...NO_WAIT,
    }));

    result.current.onStatusEntry({
      message: 'task PROJ-8: claude is asking permission to run WebFetch',
    });

    expect(fetchPendingPermissions).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();
  });

  test('non-permission status entries are NEVER suppressed by the focused-task gate', () => {
    // The suppression is permission-only; a "task started" line for the
    // focused task must still notify.
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify, {
      activeTaskId: 'PROJ-1',
    }));

    result.current.onStatusEntry({
      message: 'Mission PROJ-1: starting mission: fix the login bug',
    });

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][0].kind).toBe(NOTIFICATION_KIND.STARTED);
  });
});


describe('useNotificationRouting — onSessionEvent', () => {

  test('PERMISSION_REQUEST → ATTENTION notification with tool name in body', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.PERMISSION_REQUEST,
      request_id: 'r1',
      tool_name: 'Bash',
    }, 'T1');

    expect(notify).toHaveBeenCalledTimes(1);
    const arg = notify.mock.calls[0][0];
    expect(arg.kind).toBe(NOTIFICATION_KIND.ATTENTION);
    expect(arg.taskId).toBe('T1');
    expect(arg.body).toBe('Bash');
    expect(arg.title.toLowerCase()).toContain('approval');
  });

  test('PERMISSION_REQUEST from the live stream always notifies', () => {
    // The webserver already auto-resolves a matching pending request
    // against a remembered decision before it's ever published over
    // SSE (see _maybe_auto_resolve_live_event in kato_webserver/app.py)
    // — so onSessionEvent no longer needs (or reads) recallToolDecision
    // at all; reaching here always means a real ask.
    const notify = vi.fn();
    const recallToolDecision = vi.fn().mockReturnValue('allow');
    const { result } = renderHook(() => useNotificationRouting(notify, {
      recallToolDecision,
    }));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.PERMISSION_REQUEST,
      request_id: 'r1',
      tool_name: 'Bash',
    }, 'T1');

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][0].body).toBe('Bash');
    expect(recallToolDecision).not.toHaveBeenCalled();
  });

  test('CONTROL_REQUEST → ATTENTION notification (unpacks nested envelope)', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.CONTROL_REQUEST,
      request: { request_id: 'r2', tool_name: 'Write' },
    }, 'T2');

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][0].body).toBe('Write');
  });

  test('RESULT (ok) → REPLY kind with summary truncated to 140 chars', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.RESULT,
      is_error: false,
      result: 'a'.repeat(300),
    }, 'T1');

    expect(notify).toHaveBeenCalledTimes(1);
    const arg = notify.mock.calls[0][0];
    expect(arg.kind).toBe(NOTIFICATION_KIND.REPLY);
    expect(arg.body.length).toBe(140);
    expect(arg.title).toBe('Claude replied');
  });

  test('RESULT (error) → ERROR kind with "Turn failed" title', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.RESULT,
      is_error: true,
      result: 'rate limited',
    }, 'T1');

    expect(notify).toHaveBeenCalledTimes(1);
    const arg = notify.mock.calls[0][0];
    expect(arg.kind).toBe(NOTIFICATION_KIND.ERROR);
    expect(arg.title).toBe('Turn failed');
  });

  test('non-string result is treated as empty body (no crash)', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    result.current.onSessionEvent({
      type: CLAUDE_EVENT.RESULT,
      is_error: false,
      result: { unexpected: 'object' },
    }, 'T1');

    expect(notify.mock.calls[0][0].body).toBe('');
  });

  test('ASSISTANT / USER / SYSTEM events are NOT routed (only RESULT + permission)', () => {
    // The routing is conservative — only events that demand
    // operator action or signal a terminal state trigger
    // notifications. Mid-turn assistant/user events would spam.
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));

    for (const type of [
      CLAUDE_EVENT.ASSISTANT,
      CLAUDE_EVENT.USER,
      CLAUDE_EVENT.SYSTEM,
      CLAUDE_EVENT.STREAM_EVENT,
    ]) {
      result.current.onSessionEvent({ type }, 'T1');
    }
    expect(notify).not.toHaveBeenCalled();
  });

  test('event with no type is dropped', () => {
    const notify = vi.fn();
    const { result } = renderHook(() => useNotificationRouting(notify));
    result.current.onSessionEvent({}, 'T1');
    result.current.onSessionEvent(null, 'T1');
    expect(notify).not.toHaveBeenCalled();
  });
});
