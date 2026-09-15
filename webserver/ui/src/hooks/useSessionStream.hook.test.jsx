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
import { renderHook, act, waitFor } from '@testing-library/react';

import {
  SESSION_LIFECYCLE,
  useSessionStream,
  clearTaskStreamCache,
  reducer,
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

  test('hydrate stamps lastEventAt to now (no stale "idle for X" on tab switch)', () => {
    // The cached lastEventAt is when the operator last WATCHED the tab, not
    // when the session last acted — so hydrate resets it to now. Without this
    // a tab switch flashed "idle for Xm / may be stalled" until the backlog
    // replayed. (Pre-fix this would be 0 from the empty cache.)
    const before = Date.now();
    const { result } = renderHook(() => useSessionStream('T1'));
    expect(result.current.lastEventAt).toBeGreaterThanOrEqual(before);
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

  // Reported: "I started a new task and he did the task but there is nothing
  // on the chat at all, it is empty. I had to go to another task and come back
  // to it again to see the chat." The tab connected while the workspace was
  // still provisioning, got session_idle, closed the stream — and then never
  // heard the session kato spawned a moment later.
  test('an idle tab re-opens the stream so a later session is not missed', () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useSessionStream('T1'));
      act(() => { FakeEventSource.instances[0].emit('session_idle'); });
      expect(FakeEventSource.instances[0].closed).toBe(true);
      expect(FakeEventSource.instances.length).toBe(1);

      act(() => { vi.advanceTimersByTime(2000); });
      expect(FakeEventSource.instances.length).toBe(2);
      expect(FakeEventSource.instances[1].url).toContain('T1');
    } finally {
      vi.useRealTimers();
    }
  });

  test('the idle retry backs off while the tab stays idle', () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useSessionStream('T1'));
      act(() => { FakeEventSource.instances[0].emit('session_idle'); });
      act(() => { vi.advanceTimersByTime(2000); });
      expect(FakeEventSource.instances.length).toBe(2);

      // Still idle → the next wait is longer, so 2s is no longer enough.
      act(() => { FakeEventSource.instances[1].emit('session_idle'); });
      act(() => { vi.advanceTimersByTime(2000); });
      expect(FakeEventSource.instances.length).toBe(2);

      act(() => { vi.advanceTimersByTime(2000); });
      expect(FakeEventSource.instances.length).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  test('a live session stops the idle retry', () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useSessionStream('T1'));
      act(() => { FakeEventSource.instances[0].emit('session_idle'); });
      act(() => { vi.advanceTimersByTime(2000); });
      expect(FakeEventSource.instances.length).toBe(2);

      act(() => {
        FakeEventSource.instances[1].emit('session_event', {
          event: { raw: { type: 'assistant', message: { content: [] } } },
        });
      });
      // Streaming now — no further reconnects, however long we wait.
      act(() => { vi.advanceTimersByTime(120000); });
      expect(FakeEventSource.instances.length).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  test('a closed or missing tab is NOT retried', () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useSessionStream('T1'));
      act(() => { FakeEventSource.instances[0].emit('session_missing'); });
      act(() => { vi.advanceTimersByTime(120000); });
      expect(FakeEventSource.instances.length).toBe(1);
    } finally {
      vi.useRealTimers();
    }
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

  test('session_history_event appends to events without setting turnInFlight', async () => {
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
    // A replay is BUFFERED and applied in one dispatch — here by the quiet
    // fallback, since this stream never sends an end marker. See the replay
    // buffer in useSessionStream: applying a long transcript as it streamed
    // re-rendered and re-scrolled the log hundreds of times, which the
    // operator saw as the chat scrolling itself for 10-15 seconds.
    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThan(0);
    });
    // turnInFlight stays false — history doesn't trigger live turn.
    expect(result.current.turnInFlight).toBe(false);
  });

  test('a replayed transcript lands in ONE render pass, in order', async () => {
    // The whole point of the buffer: N history frames must not mean N
    // dispatches. Order is preserved because the batch folds through the
    // same per-event reducer, in sequence.
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      for (let i = 0; i < 25; i += 1) {
        FakeEventSource.instances[0].emit('session_history_event', {
          event: { raw: { type: 'assistant', message: { content: [
            { type: 'text', text: `line ${i}` },
          ] } } },
        });
      }
    });
    // Asserted on CONTENT, not on entry count: consecutive assistant text
    // frames legitimately merge into one bubble, and that merging is the
    // per-event reducer's business — the batch must not change it.
    await waitFor(() => {
      expect(JSON.stringify(result.current.events)).toContain('line 24');
    });
    const blob = JSON.stringify(result.current.events);
    // Nothing dropped...
    expect(blob).toContain('line 0');
    expect(blob).toContain('line 12');
    // ...and folded in arrival order, because the batch replays the same
    // reducer in sequence rather than merging the frames itself.
    expect(blob.indexOf('line 0')).toBeLessThan(blob.indexOf('line 12'));
    expect(blob.indexOf('line 12')).toBeLessThan(blob.indexOf('line 24'));
  });

  // "after refresh the chat scroll for 10 seconds to the bottom. just make
  // sure it's on the bottom." Every render mid-replay re-filters and re-pins
  // the whole growing transcript; one render at the end lands at the bottom
  // with nothing to watch.
  const notice = (text) => ({ event: { raw: { type: 'system', subtype: 'notice', text } } });

  test('a replay is not applied until the server says it has all arrived', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      for (let i = 0; i < 25; i += 1) {
        FakeEventSource.instances[0].emit('session_history_event', notice(`notice ${i}`));
      }
    });
    expect(result.current.events).toHaveLength(0);
    act(() => { FakeEventSource.instances[0].emit('session_idle'); });
    expect(result.current.events).toHaveLength(25);
  });

  test('every end marker applies the replay', () => {
    for (const marker of ['session_turn_state', 'session_closed', 'session_missing', 'session_idle']) {
      const { result, unmount } = renderHook(() => useSessionStream(`T-${marker}`));
      const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
      act(() => { source.emit('session_history_event', notice('past')); });
      expect(result.current.events, marker).toHaveLength(0);
      act(() => {
        source.emit(marker, marker === 'session_turn_state' ? { working: false } : undefined);
      });
      expect(result.current.events, marker).toHaveLength(1);
      unmount();
    }
  });

  test("the host's turn state lands AFTER the replay it corrects", () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      const source = FakeEventSource.instances[0];
      source.emit('session_history_event', notice('past'));
      // A backlog turn whose result scrolled out of the tail: on its own it
      // reads as "working".
      source.emit('session_event', {
        event: { raw: { type: 'assistant', message: { content: [] } } },
      });
      source.emit('session_turn_state', { working: false });
    });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('backlog events that arrive mid-replay keep their place in the order', () => {
    const onIncoming = vi.fn();
    const { result } = renderHook(() => useSessionStream('T1', onIncoming));
    act(() => {
      const source = FakeEventSource.instances[0];
      source.emit('session_history_event', notice('first-older'));
      source.emit('session_event', notice('second-backlog'));
      source.emit('session_history_event', notice('third-newer'));
    });
    expect(onIncoming).not.toHaveBeenCalled();
    act(() => { FakeEventSource.instances[0].emit('session_idle'); });
    const blob = JSON.stringify(result.current.events);
    expect(blob.indexOf('first-older')).toBeLessThan(blob.indexOf('second-backlog'));
    expect(blob.indexOf('second-backlog')).toBeLessThan(blob.indexOf('third-newer'));
    expect(onIncoming).toHaveBeenCalledTimes(1);
  });

  test('a replay cut short by a stream error is still shown', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => {
      FakeEventSource.instances[0].emit('session_history_event', notice('past'));
      FakeEventSource.instances[0].emitError();
    });
    expect(result.current.events).toHaveLength(1);
  });

  test('a replay that goes quiet with no end marker is shown anyway', async () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => { FakeEventSource.instances[0].emit('session_history_event', notice('past')); });
    expect(result.current.events).toHaveLength(0);
    await waitFor(() => { expect(result.current.events).toHaveLength(1); });
  });

  test('a live event with no replay underway is applied at once', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    act(() => { FakeEventSource.instances[0].emit('session_event', notice('live now')); });
    expect(JSON.stringify(result.current.events)).toContain('live now');
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


// Codex's turn lifecycle. The reducer only knew Claude's assistant/result, so
// a Codex turn set "working" and nothing cleared it: the spinner ran forever
// and the status chip only flipped to idle after switching tabs and back —
// a remount re-deriving what the stream should have reported.
describe('useSessionStream — Codex turn lifecycle', () => {
  function emit(raw) {
    act(() => {
      FakeEventSource.instances[0].emit('session_event', { event: { raw } });
    });
  }

  test('turn.started marks the turn in flight', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({ type: 'turn.started' });
    expect(result.current.turnInFlight).toBe(true);
  });

  test('turn.completed clears it — the reported bug', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({ type: 'turn.started' });
    expect(result.current.turnInFlight).toBe(true);
    emit({ type: 'turn.completed', usage: { output_tokens: 2 } });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('a failed turn clears it too', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({ type: 'turn.started' });
    emit({ type: 'turn.failed', error: { message: 'nope' } });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('an aborted turn clears it too', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({ type: 'turn.started' });
    emit({ type: 'turn.aborted', returncode: 1 });
    expect(result.current.turnInFlight).toBe(false);
  });

  test('a produced item keeps the turn alive', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({
      type: 'item.completed',
      item: { type: 'agent_message', text: 'hi' },
    });
    expect(result.current.turnInFlight).toBe(true);
  });

  test('a full turn ends idle', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emit({ type: 'thread.started', thread_id: 't1' });
    emit({ type: 'turn.started' });
    emit({ type: 'item.completed', item: { type: 'agent_message', text: 'hi' } });
    emit({ type: 'turn.completed' });
    expect(result.current.turnInFlight).toBe(false);
  });
});


// A live Codex chat replays from TWO sources at once: the CLI's rollout
// transcript on disk (everything before this kato process started) and the
// live session's in-memory log (everything since). Codex turns carry no id —
// no uuid, no message id — so with nothing to match on, every turn present in
// both rendered TWICE. It shows up the moment the operator sends a message in
// an existing chat after a restart.
describe('useSessionStream — Codex history and live never double up', () => {
  function bothSources(raw) {
    let state = reducer(
      { events: [], eventKeys: new Set(), lifecycle: 'connecting' },
      { type: 'incoming_history', event: raw, receivedAtEpoch: 0 },
    );
    state = reducer(
      state, { type: 'incoming_event', event: raw, receivedAtEpoch: 5 },
    );
    return state.events;
  }

  test('a prompt from disk and from memory renders once', () => {
    expect(bothSources({
      type: 'user',
      message: { content: [{ type: 'text', text: 'review my changes' }] },
    })).toHaveLength(1);
  });

  test('a reply from disk and from memory renders once', () => {
    expect(bothSources({
      type: 'item.completed',
      item: { type: 'agent_message', text: 'Looks good.' },
    })).toHaveLength(1);
  });

  test('DIFFERENT prompts both render', () => {
    let state = reducer(
      { events: [], eventKeys: new Set(), lifecycle: 'connecting' },
      {
        type: 'incoming_history',
        event: {
          type: 'user',
          message: { content: [{ type: 'text', text: 'first' }] },
        },
        receivedAtEpoch: 0,
      },
    );
    state = reducer(state, {
      type: 'incoming_event',
      event: {
        type: 'user',
        message: { content: [{ type: 'text', text: 'second' }] },
      },
      receivedAtEpoch: 5,
    });
    expect(state.events).toHaveLength(2);
  });

  test('a non-message item is not collapsed by content', () => {
    // Two identical commands are two real runs, not a duplicate.
    let state = reducer(
      { events: [], eventKeys: new Set(), lifecycle: 'connecting' },
      {
        type: 'incoming_event',
        event: {
          type: 'item.completed',
          item: { type: 'command_execution', command: 'npm test' },
        },
        receivedAtEpoch: 1,
      },
    );
    state = reducer(state, {
      type: 'incoming_event',
      event: {
        type: 'item.completed',
        item: { type: 'command_execution', command: 'npm test' },
      },
      receivedAtEpoch: 2,
    });
    expect(state.events).toHaveLength(2);
  });
});


// The message-loss bug behind "sometimes kato chat hide mesages. i need to
// refresh the page to see the summary of a task."
describe('useSessionStream — distinct messages are never collapsed', () => {
  function emitHistory(raw) {
    act(() => {
      FakeEventSource.instances[0].emit('session_history_event', { event: { raw } });
    });
  }

  test('events with no uuid / message id still render individually', async () => {
    // These share type+subtype+session — kato's own synthetic bubbles do —
    // and that triple used to BE the identity, so the second and every one
    // after it were dropped as duplicates and never rendered.
    const { result } = renderHook(() => useSessionStream('T1'));
    emitHistory({ type: 'system', subtype: 'notice', text: 'first notice' });
    emitHistory({ type: 'system', subtype: 'notice', text: 'task summary here' });
    emitHistory({ type: 'system', subtype: 'notice', text: 'third notice' });

    await waitFor(() => {
      expect(result.current.events.length).toBe(3);
    });
    const blob = JSON.stringify(result.current.events);
    expect(blob).toContain('task summary here');
    expect(blob).toContain('third notice');
  });

  test('but an identical replay of the SAME record still dedupes', async () => {
    // The property the fingerprint exists for: reopening a chat replays the
    // JSONL, and that must not double every bubble.
    const { result } = renderHook(() => useSessionStream('T1'));
    const record = { type: 'system', subtype: 'notice', text: 'same exact text' };
    emitHistory({ ...record });
    emitHistory({ ...record });

    await waitFor(() => {
      expect(JSON.stringify(result.current.events)).toContain('same exact text');
    });
    expect(result.current.events.length).toBe(1);
  });
});


// "if it's still running why i dont see the animated label?"
//
// Background work outlives the turn that launched it. ``awaitingBackground``
// was a property of the TURN, so the operator asking "are you still running?"
// mid-wait wiped it: the agent answered, that reply was a turn with no
// background tool in it, and the tab fell back to ``idle`` with a coverage
// suite still going.
describe('useSessionStream — waiting on background work', () => {
  const bgTool = {
    type: 'assistant',
    message: { id: 'm-bg', content: [
      { type: 'tool_use', name: 'Bash', input: { run_in_background: true } },
    ] },
  };
  const plainReply = (id, text) => ({
    type: 'assistant',
    message: { id, content: [{ type: 'text', text }] },
  });
  const result = (id) => ({ type: 'result', uuid: id });

  function emit(raw) {
    act(() => {
      FakeEventSource.instances[0].emit('session_event', { event: { raw } });
    });
  }

  test('a launched background job leaves the session waiting, not idle', () => {
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit(bgTool);
    emit(result('r1'));
    expect(hook.current.turnInFlight).toBe(false);
    expect(hook.current.awaitingBackground).toBe(true);
  });

  test('a LATER turn that starts nothing does not cancel the wait', () => {
    // The exact reported sequence: ask "are you still running?", get an
    // answer, and the status must not drop back to idle.
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit(bgTool);
    emit(result('r1'));

    emit(plainReply('m-answer', 'Yes, about half done.'));
    expect(hook.current.turnInFlight).toBe(true);   // that reply IS a live turn
    emit(result('r2'));

    expect(hook.current.turnInFlight).toBe(false);
    expect(hook.current.awaitingBackground).toBe(true);
  });

  test('the job reporting back is what ends the wait', () => {
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit(bgTool);
    emit(result('r1'));
    expect(hook.current.awaitingBackground).toBe(true);

    emit({
      type: 'user',
      uuid: 'u-notify',
      message: { content: '<task-notification>\n<task-id>abc</task-id>\n' },
    });

    expect(hook.current.awaitingBackground).toBe(false);
  });

  test('an ordinary user message does NOT end the wait', () => {
    // Only a task notification means the work reported; a person typing
    // does not.
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit(bgTool);
    emit(result('r1'));
    emit({ type: 'user', uuid: 'u-human', message: { content: 'any update?' } });
    expect(hook.current.awaitingBackground).toBe(true);
  });

  test('a Workflow is reported as a workflow, and that survives too', () => {
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit({
      type: 'assistant',
      message: { id: 'm-wf', content: [{ type: 'tool_use', name: 'Workflow', input: {} }] },
    });
    emit(result('r1'));
    emit(plainReply('m-answer', 'still going'));
    emit(result('r2'));

    expect(hook.current.awaitingBackground).toBe(true);
    expect(hook.current.backgroundIsWorkflow).toBe(true);
  });

  test('a session that closes drops the wait', () => {
    // The flag must not outlive the session it belongs to — a "background"
    // chip standing over a dead session is the stale-status failure this
    // codebase keeps relearning.
    const { result: hook } = renderHook(() => useSessionStream('T1'));
    emit(bgTool);
    emit(result('r1'));
    act(() => { FakeEventSource.instances[0].emit('session_closed', {}); });
    expect(hook.current.awaitingBackground).toBe(false);
  });
});

// "tab look green, i focus on it, he become working. can you make sure that
// status is always true"
//
// The connect backlog arrives as ordinary ``session_event`` frames, so the
// reducer walks it as if it were happening now. A trailing ``assistant``
// whose ``result`` has scrolled out of the bounded tail therefore left
// ``turnInFlight`` true on a session doing nothing — focusing a task turned
// its green tab yellow. The host now states the truth after the replay.
describe('useSessionStream — the host corrects a stale replay', () => {
  function emitLive(raw) {
    act(() => {
      FakeEventSource.instances[0].emit('session_event', { event: { raw } });
    });
  }

  function emitTurnState(working) {
    act(() => {
      FakeEventSource.instances[0].emit('session_turn_state', { working });
    });
  }

  test('a result-less replayed turn does not leave the task "working"', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    // The backlog's tail: the agent spoke, and its ``result`` is no longer in
    // the buffer.
    emitLive({
      type: 'assistant',
      message: { id: 'm1', content: [{ type: 'text', text: 'done ages ago' }] },
    });
    expect(result.current.turnInFlight).toBe(true);

    // ...then the host says what is actually true.
    emitTurnState(false);
    expect(result.current.turnInFlight).toBe(false);
  });

  test('a session that IS mid-turn stays working', () => {
    // The correction runs in both directions — it is the host's answer, not
    // a "clear it" hack.
    const { result } = renderHook(() => useSessionStream('T1'));
    emitTurnState(true);
    expect(result.current.turnInFlight).toBe(true);
  });

  test('live events after the correction still win', () => {
    // The frame is a one-shot reconcile at connect, not a lock.
    const { result } = renderHook(() => useSessionStream('T1'));
    emitTurnState(false);
    emitLive({
      type: 'assistant',
      message: { id: 'm2', content: [{ type: 'text', text: 'working now' }] },
    });
    expect(result.current.turnInFlight).toBe(true);
  });

  test('a malformed frame is ignored rather than crashing the stream', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emitLive({
      type: 'assistant',
      message: { id: 'm3', content: [{ type: 'text', text: 'hi' }] },
    });
    act(() => {
      FakeEventSource.instances[0].emit('session_turn_state', undefined);
    });
    // Unchanged, and still usable.
    expect(result.current.turnInFlight).toBe(true);
  });
});

// "moving to the tab only then mark the agent as working. which is not true"
//
// The other half of the same replay problem. ``markTurnBusy(false)`` clears
// ``turnInFlight`` but deliberately PRESERVES ``awaitingBackground`` — a job
// from an earlier turn must outlive a turn that starts none. Replaying the
// connect backlog re-reads an old Monitor / Workflow tool_use and re-sticks
// that flag, so focusing a task painted its tab busy and nothing could clear
// it: the corrective frame only spoke to half the state.
//
// The server's ``is_working`` already covers BOTH cases (an in-flight turn and
// a closed turn still blocked on background work), so ``working: false`` means
// idle in every sense.
describe('useSessionStream — the host correction covers the background wait', () => {
  function emitLive(raw) {
    act(() => {
      FakeEventSource.instances[0].emit('session_event', { event: { raw } });
    });
  }
  function emitTurnState(working) {
    act(() => {
      FakeEventSource.instances[0].emit('session_turn_state', { working });
    });
  }
  const monitorCall = {
    type: 'assistant',
    message: { id: 'm-bg', content: [{ type: 'tool_use', name: 'Monitor', input: {} }] },
  };
  const resultEvent = (id) => ({ type: 'result', uuid: id, subtype: 'success' });

  test('a replayed background wait does not leave the tab busy', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    // The backlog replays a long-finished turn that once started a Monitor.
    emitLive(monitorCall);
    emitLive(resultEvent('r1'));
    expect(result.current.awaitingBackground).toBe(true);

    emitTurnState(false);
    expect(result.current.awaitingBackground).toBe(false);
    expect(result.current.turnInFlight).toBe(false);
  });

  test('a replayed Workflow is cleared too, not just the generic wait', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emitLive({
      type: 'assistant',
      message: { id: 'm-wf', content: [{ type: 'tool_use', name: 'Workflow', input: {} }] },
    });
    emitLive(resultEvent('r1'));
    expect(result.current.backgroundIsWorkflow).toBe(true);

    emitTurnState(false);
    expect(result.current.backgroundIsWorkflow).toBe(false);
    expect(result.current.awaitingBackground).toBe(false);
  });

  test('the correction also drops a half-open turn\'s pending wait', () => {
    // The tool_use replayed but its result did not — so ``turnHasBackgroundWait``
    // is set and no RESULT has consumed it yet. Left behind, the NEXT result
    // (a real, unrelated turn) would resurrect the wait from a turn long over.
    const { result } = renderHook(() => useSessionStream('T1'));
    emitLive(monitorCall);
    emitTurnState(false);
    expect(result.current.awaitingBackground).toBe(false);

    emitLive({
      type: 'assistant',
      message: { id: 'm-new', content: [{ type: 'text', text: 'a new turn' }] },
    });
    emitLive(resultEvent('r2'));
    expect(result.current.awaitingBackground).toBe(false);
  });

  test('a genuinely busy session is still reported busy', () => {
    const { result } = renderHook(() => useSessionStream('T1'));
    emitTurnState(true);
    expect(result.current.turnInFlight).toBe(true);
  });

  test('a background wait started AFTER the correction still sticks', () => {
    // The frame is a one-shot reconcile at connect, not a mute switch.
    const { result } = renderHook(() => useSessionStream('T1'));
    emitTurnState(false);
    emitLive(monitorCall);
    emitLive(resultEvent('r3'));
    expect(result.current.awaitingBackground).toBe(true);
  });
});
