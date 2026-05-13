// Tests for useNotificationRouting — translates SSE events into
// notify() calls with the right kind, title, body, taskId. This
// is the bridge between the status feed / session stream and the
// notification surface; a bug here silently drops notifications.

import { describe, test, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useNotificationRouting } from './useNotificationRouting.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';


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
