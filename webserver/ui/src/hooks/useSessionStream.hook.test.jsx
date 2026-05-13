// Hook-level tests for useSessionStream. These complement the
// pure-reducer tests in useSessionStream.test.js by exercising the
// FULL hook including the EventSource lifecycle:
//
//   - Opens EventSource on mount with the correct URL.
//   - Closes EventSource on unmount.
//   - Reconnect (sent message → respawn) closes the old stream and
//     opens a new one.
//   - SSE events drive lifecycle transitions correctly:
//       session_event → STREAMING + entries appended
//       session_idle → IDLE
//       session_closed → CLOSED + turnInFlight reset (Bug A fix)
//       session_missing → MISSING + turnInFlight reset
//   - The local-cache write happens so a remount can re-hydrate.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import {
  SESSION_LIFECYCLE,
  useSessionStream,
  clearTaskStreamCache,
} from './useSessionStream.js';


// A controllable EventSource fake. Captures listeners so tests
// can drive specific SSE events at will.
class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = 1;  // OPEN
    this.listeners = new Map();
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name, cb) {
    if (!this.listeners.has(name)) { this.listeners.set(name, []); }
    this.listeners.get(name).push(cb);
  }

  close() {
    this.closed = true;
    this.readyState = 2;  // CLOSED
  }

  // Test helper — invoke a named SSE event with given JSON data.
  emit(name, data) {
    const cbs = this.listeners.get(name) || [];
    const event = { data: typeof data === 'string' ? data : JSON.stringify(data) };
    for (const cb of cbs) { cb(event); }
  }

  emitError() {
    this.readyState = 2;  // CLOSED
    if (this.onerror) { this.onerror({}); }
  }
}


beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource;
  // Constants used by the hook's readyState guard.
  globalThis.EventSource.CLOSED = 2;
  clearTaskStreamCache('T1');
  clearTaskStreamCache('T2');
});


describe('useSessionStream — EventSource lifecycle', () => {

  test('opens an EventSource on mount with the encoded task id in the URL', () => {
    renderHook(() => useSessionStream('TASK 1'));

    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].url).toContain('TASK%201');
    expect(FakeEventSource.instances[0].url).toMatch(/\/events$/);
  });

  test('no EventSource is opened when taskId is empty', () => {
    renderHook(() => useSessionStream(''));
    expect(FakeEventSource.instances.length).toBe(0);
  });

  test('closes the EventSource on unmount', () => {
    const { unmount } = renderHook(() => useSessionStream('T1'));
    expect(FakeEventSource.instances[0].closed).toBe(false);
    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  test('changing taskId closes old stream and opens a new one', () => {
    const { rerender } = renderHook(({ id }) => useSessionStream(id), {
      initialProps: { id: 'T1' },
    });
    expect(FakeEventSource.instances).toHaveLength(1);

    rerender({ id: 'T2' });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.instances[1].closed).toBe(false);
  });

  test('reconnect() spawns a fresh EventSource (used after sendMessage)', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => { result.current.reconnect(); });

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  test('reconnect() preserves STREAMING lifecycle from the cache (Bug B fix)', () => {
    // Operator-UX: after sendMessage triggers a respawn, the hook
    // calls reconnect(). Cache says we were STREAMING. Previously,
    // hydrate forced CONNECTING, flashing "Connecting to session…"
    // briefly. Now STREAMING is preserved so the banner stays
    // suppressed.
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.STREAMING);

    act(() => { result.current.reconnect(); });

    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.STREAMING);
  });

  test('reconnect() preserves IDLE lifecycle (re-open after idle)', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => { FakeEventSource.instances[0].emit('session_idle'); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.IDLE);

    act(() => { result.current.reconnect(); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.IDLE);
  });

  test('reconnect() DOES reset CLOSED to CONNECTING (correct behavior)', () => {
    // CLOSED means the prior state is stale; reconnect legitimately
    // needs to discover the new server state.
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => { FakeEventSource.instances[0].emit('session_closed'); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.CLOSED);

    act(() => { result.current.reconnect(); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.CONNECTING);
  });
});


describe('useSessionStream — incoming events drive lifecycle', () => {

  test('initial lifecycle is CONNECTING', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.CONNECTING);
  });

  test('first session_event flips lifecycle to STREAMING', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.STREAMING);
  });

  test('session_event sets turnInFlight=true on ASSISTANT type', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    expect(result.current.turnInFlight).toBe(true);
  });

  test('session_event clears turnInFlight on RESULT', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    // Start a turn.
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    expect(result.current.turnInFlight).toBe(true);
    // End the turn.
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'result' } },
      });
    });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('session_idle → IDLE lifecycle + stream closes + turnInFlight resets (Bug A)', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    // Mid-turn.
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    expect(result.current.turnInFlight).toBe(true);

    act(() => { FakeEventSource.instances[0].emit('session_idle'); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.IDLE);
    expect(result.current.turnInFlight).toBe(false);  // Bug A fix
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  test('session_missing → MISSING lifecycle + turnInFlight resets', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    act(() => { FakeEventSource.instances[0].emit('session_missing'); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.MISSING);
    expect(result.current.turnInFlight).toBe(false);
  });

  test('session_closed → CLOSED lifecycle + turnInFlight resets', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
    });
    act(() => { FakeEventSource.instances[0].emit('session_closed'); });
    expect(result.current.lifecycle).toBe(SESSION_LIFECYCLE.CLOSED);
    expect(result.current.turnInFlight).toBe(false);
  });

  test('session_history_event appends to events without setting turnInFlight', () => {
    // Replayed history must NOT make the UI think Claude is
    // actively working. ASSISTANT-shaped HISTORY events are scrollback,
    // not live turn signals.
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_history_event', {
        event: { raw: { type: 'assistant', message: { content: [
          { type: 'text', text: 'past reply' },
        ] } } },
      });
    });
    // turnInFlight stays false — history doesn't trigger live turn.
    expect(result.current.turnInFlight).toBe(false);
    // But the event is in the log so EventLog can render it.
    expect(result.current.events.length).toBeGreaterThan(0);
  });

  test('permission_request event sets pendingPermission', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: {
          type: 'permission_request',
          request_id: 'req-1',
          tool_name: 'Bash',
        } },
      });
    });
    expect(result.current.pendingPermission).toBeTruthy();
    expect(result.current.pendingPermission.request_id).toBe('req-1');
  });

  test('control_request event sets pendingPermission', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: {
          type: 'control_request',
          request_id: 'req-2',
          request: { tool_name: 'Edit' },
        } },
      });
    });
    expect(result.current.pendingPermission).toBeTruthy();
  });

  test('permission_response with matching request_id clears pendingPermission', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: {
          type: 'permission_request', request_id: 'req-1', tool_name: 'Bash',
        } },
      });
    });
    expect(result.current.pendingPermission).toBeTruthy();

    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: {
          type: 'permission_response', request_id: 'req-1', allow: true,
        } },
      });
    });
    expect(result.current.pendingPermission).toBeNull();
  });
});


describe('useSessionStream — imperative state operations', () => {

  test('appendLocalEvent adds a LOCAL-source entry that survives dedupe', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      result.current.appendLocalEvent({
        source: 'local',
        kind: 'user',
        text: 'echo bubble',
      });
    });
    expect(result.current.events.length).toBeGreaterThan(0);
    expect(
      result.current.events.some(
        (e) => e.text === 'echo bubble' || e.raw?.text === 'echo bubble',
      ),
    ).toBe(true);
  });

  test('markTurnBusy(true) sets turnInFlight without an SSE event', () => {
    // Used by MessageForm after submit: optimistically shows
    // "Claude is thinking" before the server's first ASSISTANT.
    const { result } = renderHook(() => useSessionStream('T1'));
    expect(result.current.turnInFlight).toBe(false);
    act(() => { result.current.markTurnBusy(true); });
    expect(result.current.turnInFlight).toBe(true);
  });

  test('markTurnBusy(false) clears turnInFlight', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => { result.current.markTurnBusy(true); });
    act(() => { result.current.markTurnBusy(false); });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('dismissPermission clears pendingPermission without sending an SSE', () => {
    // User clicked "x" on the modal without choosing. State reverts
    // so the modal doesn't redraw, but the backend's pending request
    // still exists (handled separately).
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: {
          type: 'permission_request', request_id: 'r', tool_name: 'Bash',
        } },
      });
    });
    expect(result.current.pendingPermission).toBeTruthy();

    act(() => { result.current.dismissPermission(); });
    expect(result.current.pendingPermission).toBeNull();
  });
});


describe('useSessionStream — onIncomingEvent callback', () => {

  test('fires for live session_events with the raw event + taskId', () => {
    const onIncoming = vi.fn();
    renderHook(() => useSessionStream('T1', onIncoming));

    act(() => {
      FakeEventSource.instances[0].emit('session_event', {
        event: { raw: { type: 'assistant' } },
      });
    });

    expect(onIncoming).toHaveBeenCalledTimes(1);
    expect(onIncoming.mock.calls[0][0]).toEqual({ type: 'assistant' });
    expect(onIncoming.mock.calls[0][1]).toBe('T1');
  });

  test('does NOT fire for history events (those are replay, not live)', () => {
    const onIncoming = vi.fn();
    renderHook(() => useSessionStream('T1', onIncoming));

    act(() => {
      FakeEventSource.instances[0].emit('session_history_event', {
        event: { raw: { type: 'assistant' } },
      });
    });

    expect(onIncoming).not.toHaveBeenCalled();
  });
});
