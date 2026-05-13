// Tests for useStatusFeed — the status-bar SSE consumer. Contract:
//   - Opens EventSource on /api/status/events.
//   - Dedupes entries by sequence.
//   - Heartbeat entries ("Idle · next scan in ...") update ``latest``
//     but do NOT push into ``history`` (prevents idle ticks from
//     evicting real activity from the rolling buffer).
//   - history capped at HISTORY_LIMIT (oldest dropped).
//   - status_disabled event closes the stream and marks stale.
//   - The onEntry callback fires on every entry.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useStatusFeed } from './useStatusFeed.js';


class FakeEventSource {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = new Map();
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, cb) {
    if (!this.listeners.has(name)) { this.listeners.set(name, []); }
    this.listeners.get(name).push(cb);
  }
  close() { this.readyState = 2; }
  emit(name, payload) {
    const cbs = this.listeners.get(name) || [];
    const event = {
      data: typeof payload === 'string' ? payload : JSON.stringify(payload),
    };
    for (const cb of cbs) { cb(event); }
  }
}


beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource;
  globalThis.EventSource.CLOSED = 2;
});


describe('useStatusFeed', () => {

  test('opens EventSource on /api/status/events', () => {
    renderHook(() => useStatusFeed());
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].url).toBe('/api/status/events');
  });

  test('initial state: latest=null, history=[], connected=false', () => {
    const { result } = renderHook(() => useStatusFeed());
    expect(result.current.latest).toBeNull();
    expect(result.current.history).toEqual([]);
    expect(result.current.connected).toBe(false);
  });

  test('status_entry pushes onto history AND updates latest', () => {
    const { result } = renderHook(() => useStatusFeed());
    act(() => {
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 1, message: 'Mission T1: starting mission',
      });
    });
    expect(result.current.latest?.sequence).toBe(1);
    expect(result.current.history.length).toBe(1);
  });

  test('dedupe: a duplicate sequence is ignored', () => {
    const { result } = renderHook(() => useStatusFeed());
    act(() => {
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 7, message: 'event 7',
      });
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 7, message: 'event 7 again',
      });
    });
    expect(result.current.history.length).toBe(1);
    expect(result.current.latest.message).toBe('event 7');
  });

  test('heartbeat entries update latest but NOT history', () => {
    // The contract: idle heartbeats keep the live bar alive
    // (countdown timer) without polluting the scrollback that the
    // operator reads.
    const { result } = renderHook(() => useStatusFeed());
    act(() => {
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 1, message: 'Mission T1: starting mission',
      });
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 2, message: 'Idle · next scan in 30s',
      });
    });
    // history has the real entry but NOT the heartbeat.
    expect(result.current.history.length).toBe(1);
    expect(result.current.history[0].sequence).toBe(1);
    // latest is the most recent (heartbeat).
    expect(result.current.latest.sequence).toBe(2);
  });

  test('history is capped at HISTORY_LIMIT (oldest dropped)', () => {
    // The cap is 200; pushing 250 should leave history at 200.
    const { result } = renderHook(() => useStatusFeed());
    act(() => {
      for (let i = 0; i < 250; i += 1) {
        FakeEventSource.instances[0].emit('status_entry', {
          sequence: i, message: `Mission X: event ${i}`,
        });
      }
    });
    expect(result.current.history.length).toBe(200);
    // Oldest dropped — first entry is sequence 50 (0..49 evicted).
    expect(result.current.history[0].sequence).toBe(50);
  });

  test('status_disabled marks stale and closes the stream', () => {
    const { result } = renderHook(() => useStatusFeed());
    act(() => { FakeEventSource.instances[0].emit('status_disabled'); });
    expect(result.current.stale).toBe(true);
    expect(result.current.connected).toBe(false);
    expect(FakeEventSource.instances[0].readyState).toBe(2);
  });

  test('onEntry callback fires for each entry', () => {
    const onEntry = vi.fn();
    renderHook(() => useStatusFeed(onEntry));
    act(() => {
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 1, message: 'test',
      });
    });
    expect(onEntry).toHaveBeenCalledTimes(1);
    expect(onEntry.mock.calls[0][0].sequence).toBe(1);
  });

  test('onEntry receives the latest callback reference (no stale-closure)', () => {
    // The hook stores onEntry in a ref so it can change without
    // re-subscribing the SSE. A future rerender with a new
    // callback must fire that callback, not the old one.
    const oldCb = vi.fn();
    const newCb = vi.fn();
    const { rerender } = renderHook(
      ({ cb }) => useStatusFeed(cb),
      { initialProps: { cb: oldCb } },
    );
    rerender({ cb: newCb });
    act(() => {
      FakeEventSource.instances[0].emit('status_entry', {
        sequence: 1, message: 'test',
      });
    });
    expect(oldCb).not.toHaveBeenCalled();
    expect(newCb).toHaveBeenCalledTimes(1);
  });

  test('unmount closes the EventSource', () => {
    const { unmount } = renderHook(() => useStatusFeed());
    const stream = FakeEventSource.instances[0];
    expect(stream.readyState).toBe(1);
    unmount();
    expect(stream.readyState).toBe(2);
  });
});
