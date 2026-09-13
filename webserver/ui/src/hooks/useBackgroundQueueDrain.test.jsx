// "feels like i need to move to the tab to make the agent start..."
//
// Drives the REAL hook, the REAL decision engine, the REAL queue store and the
// REAL status derivation. Only the network (postChatMessage) and IndexedDB
// (jsdom has none) are stood in for.

import { beforeEach, describe, expect, test, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

const { _mem, _posts } = vi.hoisted(() => ({
  _mem: new Map(),
  _posts: { calls: [], result: { ok: true, status: 200, body: {} }, gate: null },
}));

vi.mock('../utils/idbStore.js', () => ({
  idbGet: (key) => Promise.resolve(_mem.get(key)),
  idbSet: async (key, value) => { _mem.set(key, value); },
  idbDelete: async (key) => { _mem.delete(key); },
}));

vi.mock('../api.js', () => ({
  postChatMessage: async (taskId, text, images, backend) => {
    // Recorded at the START of the send, so a held-open request still counts.
    _posts.calls.push({ taskId, text, backend });
    if (_posts.gate) { await _posts.gate; }
    return _posts.result;
  },
}));

const { useBackgroundQueueDrain } = await import('./useBackgroundQueueDrain.js');
const {
  readQueuedMessages, writeQueuedMessages, _resetQueuedMessagesStore,
} = await import('../utils/queuedMessagesStore.js');
const { toastStore } = await import('../stores/toastStore.js');

const session = (taskId, working) => ({
  task_id: taskId, working, live: true, status: 'active', agent_backend: 'claude',
});
const queue = (...texts) => texts.map((text, i) => ({ id: `q${i}-${text}`, text, images: [] }));

function mount(initial) {
  return renderHook((props) => useBackgroundQueueDrain(props), { initialProps: initial });
}

beforeEach(() => {
  _mem.clear();
  _posts.calls = [];
  _posts.result = { ok: true, status: 200, body: {} };
  _posts.gate = null;
  _resetQueuedMessagesStore();
});

describe('background queue drain — a task off screen still runs its queue', () => {
  test('a background task\'s turn ending sends its next prompt without opening the tab', async () => {
    writeQueuedMessages('BG', queue('second prompt'));
    const hook = mount({ sessions: [session('BG', true)], activeTaskId: 'OTHER', agentStatuses: {} });
    hook.rerender({ sessions: [session('BG', false)], activeTaskId: 'OTHER', agentStatuses: {} });

    await waitFor(() => expect(_posts.calls).toHaveLength(1));
    expect(_posts.calls[0]).toMatchObject({ taskId: 'BG', text: 'second prompt', backend: 'claude' });
    expect(readQueuedMessages('BG')).toEqual([]);
  });

  test('ONE prompt per turn — a stale idle poll cannot release the next one', async () => {
    writeQueuedMessages('BG', queue('one', 'two', 'three'));
    const props = (working) => ({ sessions: [session('BG', working)], activeTaskId: 'X', agentStatuses: {} });
    const hook = mount(props(true));
    hook.rerender(props(false));                       // turn ended -> "one"
    await waitFor(() => expect(_posts.calls).toHaveLength(1));

    hook.rerender(props(false));                       // stale: "one" not started
    hook.rerender(props(false));
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(1);

    hook.rerender(props(true));                        // "one" running
    hook.rerender(props(false));                       // "one" done -> "two"
    await waitFor(() => expect(_posts.calls).toHaveLength(2));
    expect(_posts.calls.map((c) => c.text)).toEqual(['one', 'two']);
    expect(readQueuedMessages('BG').map((m) => m.text)).toEqual(['three']);
  });

  test('an idle task on first load does NOT fire its queue', async () => {
    writeQueuedMessages('BG', queue('waiting'));
    mount({ sessions: [session('BG', false)], activeTaskId: 'X', agentStatuses: {} });
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(0);
  });

  test('the FOCUSED task is left to its own chat — no double send', async () => {
    writeQueuedMessages('FOCUS', queue('mine'));
    const hook = mount({ sessions: [session('FOCUS', true)], activeTaskId: 'FOCUS', agentStatuses: {} });
    hook.rerender({ sessions: [session('FOCUS', false)], activeTaskId: 'FOCUS', agentStatuses: {} });
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(0);
  });

  test('switching AWAY from a task is not that task\'s turn ending', async () => {
    writeQueuedMessages('T', queue('queued'));
    const hook = mount({ sessions: [session('T', true)], activeTaskId: 'T', agentStatuses: {} });
    // Now it is in the background and idle — but no turn was seen ending.
    hook.rerender({ sessions: [session('T', false)], activeTaskId: 'OTHER', agentStatuses: {} });
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(0);
  });

  test('a failed send is never silent and never loses the prompt', async () => {
    writeQueuedMessages('BG', queue('keep me', 'after'));
    _posts.result = { ok: false, status: 500, error: 'agent unavailable' };
    const toasts = [];
    const stop = toastStore.subscribe((list) => { toasts.push(...list); });

    const hook = mount({ sessions: [session('BG', true)], activeTaskId: 'X', agentStatuses: {} });
    hook.rerender({ sessions: [session('BG', false)], activeTaskId: 'X', agentStatuses: {} });

    await waitFor(() => expect(toasts.some((t) => /queued prompt was not sent/.test(t.title))).toBe(true));
    const shown = toasts.find((t) => /queued prompt was not sent/.test(t.title));
    expect(shown.kind).toBe('error');
    expect(shown.durationMs).toBe(0);
    expect(shown.message).toMatch(/agent unavailable/);
    // Back at the FRONT, order intact.
    expect(readQueuedMessages('BG').map((m) => m.text)).toEqual(['keep me', 'after']);
    stop();
  });

  test('a task paused on an approval is busy — its queue waits', async () => {
    writeQueuedMessages('BG', queue('later'));
    const hook = mount({ sessions: [session('BG', true)], activeTaskId: 'X', agentStatuses: {} });
    hook.rerender({
      sessions: [{ ...session('BG', false), has_pending_permission: true }],
      activeTaskId: 'X', agentStatuses: {},
    });
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(0);
  });

  test('a cold queue is read from durable storage after a reload', async () => {
    _mem.set('kato.queued-messages.BG', queue('from before the reload'));
    const hook = mount({ sessions: [session('BG', true)], activeTaskId: 'X', agentStatuses: {} });
    hook.rerender({ sessions: [session('BG', false)], activeTaskId: 'X', agentStatuses: {} });
    await waitFor(() => expect(_posts.calls).toHaveLength(1));
    expect(_posts.calls[0].text).toBe('from before the reload');
  });
});

// Two guards that mutation testing showed the tests above could not reach.
describe('background queue drain — guards against a double send', () => {
  test('a task seen busy, then focused, then left idle does NOT send again', async () => {
    // T is busy in the background, the operator opens it, its turn ends while
    // focused (its own chat delivers the next prompt), and the operator leaves.
    // Without forgetting T on focus the engine still remembers "busy", so the
    // first idle reading after leaving looks like a turn ending — a second
    // prompt for a turn that already had its delivery.
    writeQueuedMessages('T', queue('already handled by the chat'));
    const hook = mount({ sessions: [session('T', true)], activeTaskId: 'X', agentStatuses: {} });
    hook.rerender({ sessions: [session('T', true)], activeTaskId: 'T', agentStatuses: {} });
    hook.rerender({ sessions: [session('T', false)], activeTaskId: 'T', agentStatuses: {} });
    hook.rerender({ sessions: [session('T', false)], activeTaskId: 'X', agentStatuses: {} });
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(0);
  });

  test('a second turn ending while a send is still in flight does not start another', async () => {
    // A slow server holds the first send open. Two poll cycles later another
    // busy -> idle edge arrives; without the in-flight guard prompt two would
    // start while prompt one is still being delivered.
    writeQueuedMessages('BG', queue('one', 'two'));
    let release;
    _posts.gate = new Promise((resolve) => { release = resolve; });
    const props = (working) => ({ sessions: [session('BG', working)], activeTaskId: 'X', agentStatuses: {} });

    const hook = mount(props(true));
    hook.rerender(props(false));                 // edge 1 -> "one" starts, held
    await waitFor(() => expect(_posts.calls).toHaveLength(1));
    hook.rerender(props(true));
    hook.rerender(props(false));                 // edge 2 while "one" is in flight
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls).toHaveLength(1);

    release();
    await act(async () => { await Promise.resolve(); });
    expect(_posts.calls.map((c) => c.text)).toEqual(['one']);
    expect(readQueuedMessages('BG').map((m) => m.text)).toEqual(['two']);
  });
});
