// Tests for permissionStore — the single source of truth for pending
// tool-approval asks across ALL tasks. Verifies the poll reconcile, the
// instant SSE push, optimistic resolve + tombstone (no resurrection by a
// racing poll), the per-task pending query, and the audit-sink registry.

import { describe, test, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({ fetchPendingPermissions: vi.fn() }));
vi.mock('../api.js', () => api);

import { permissionStore } from './permissionStore.js';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function _ask(taskId, requestId, tool = 'Bash') {
  return {
    task_id: taskId,
    type: 'control_request',
    request_id: requestId,
    request: { request_id: requestId, tool_name: tool, input: { command: 'mvn' } },
  };
}

function track() {
  const seen = [];
  const unsubscribe = permissionStore.subscribe((snap) => seen.push(snap));
  return { seen, unsubscribe, last: () => seen[seen.length - 1] };
}

beforeEach(() => {
  permissionStore.__resetForTests();
  api.fetchPendingPermissions.mockReset();
  api.fetchPendingPermissions.mockResolvedValue({ pending: [] });
});


describe('permissionStore', () => {
  test('poll surfaces pending asks for every task', async () => {
    api.fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('T1', 'r1'), _ask('T2', 'r2')],
    });
    const sub = track();
    await flush();
    const ids = sub.last().list.map((e) => e.request_id);
    expect(ids).toEqual(['r1', 'r2']);
    sub.unsubscribe();
    // Clean up so later tests start empty.
    permissionStore.resolve('r1');
    permissionStore.resolve('r2');
  });

  test('push() surfaces the focused ask instantly before the poll', async () => {
    const sub = track();
    await flush(); // initial empty poll
    permissionStore.push('T9', _ask('T9', 'live-1'), 'a summary');
    expect(sub.last().list.map((e) => e.request_id)).toEqual(['live-1']);
    // The envelope is stamped with task id + summary for the modal title.
    expect(sub.last().list[0].task_id).toBe('T9');
    expect(sub.last().list[0].task_summary).toBe('a summary');
    sub.unsubscribe();
    permissionStore.resolve('live-1');
  });

  test('hasPendingForTask reflects the current asks', async () => {
    api.fetchPendingPermissions.mockResolvedValue({ pending: [_ask('T1', 'r1')] });
    const sub = track();
    await flush();
    expect(permissionStore.hasPendingForTask('T1')).toBe(true);
    expect(permissionStore.hasPendingForTask('T2')).toBe(false);
    sub.unsubscribe();
    permissionStore.resolve('r1');
  });

  test('resolve() removes the ask and a racing poll does NOT resurrect it', async () => {
    api.fetchPendingPermissions.mockResolvedValue({ pending: [_ask('T1', 'r1')] });
    const sub = track();
    await flush();
    expect(sub.last().list).toHaveLength(1);

    // Operator approves → resolve() tombstones it.
    permissionStore.resolve('r1');
    expect(sub.last().list).toHaveLength(0);

    // A poll that still reports the ask (server hasn't dropped it yet)
    // must NOT bring it back — the tombstone blocks resurrection.
    await permissionStore.refresh();
    await flush();
    expect(sub.last().list).toHaveLength(0);
    sub.unsubscribe();
  });

  test('the next poll sends the signature of the list it holds', async () => {
    api.fetchPendingPermissions.mockResolvedValue({ pending: [_ask('T1', 'r1')], signature: 'sig-1' });
    const sub = track();
    await flush();
    await permissionStore.refresh();
    expect(api.fetchPendingPermissions.mock.calls.map((c) => c[0])).toEqual(['', 'sig-1']);
    sub.unsubscribe();
    permissionStore.resolve('r1');
  });

  test('an unchanged answer keeps the asks it confirmed on screen', async () => {
    api.fetchPendingPermissions.mockResolvedValueOnce({ pending: [_ask('T1', 'r1')], signature: 'sig-1' });
    api.fetchPendingPermissions.mockResolvedValue({ unchanged: true });
    const sub = track();
    await flush();
    await permissionStore.refresh();
    await permissionStore.refresh();
    expect(sub.last().list.map((e) => e.request_id)).toEqual(['r1']);
    // Still the same signature: "unchanged" never replaces what it confirms.
    expect(api.fetchPendingPermissions.mock.calls.at(-1)[0]).toBe('sig-1');
    sub.unsubscribe();
    permissionStore.resolve('r1');
  });

  test('an unchanged answer does not bring back an ask already answered', async () => {
    api.fetchPendingPermissions.mockResolvedValueOnce({ pending: [_ask('T1', 'r1')], signature: 'sig-1' });
    api.fetchPendingPermissions.mockResolvedValue({ unchanged: true });
    const sub = track();
    await flush();
    permissionStore.resolve('r1');
    await permissionStore.refresh();
    expect(sub.last().list).toHaveLength(0);
    sub.unsubscribe();
  });

  test('a new list replaces the old one and its signature', async () => {
    api.fetchPendingPermissions.mockResolvedValueOnce({ pending: [_ask('T1', 'r1')], signature: 'sig-1' });
    api.fetchPendingPermissions.mockResolvedValueOnce({ pending: [_ask('T2', 'r2')], signature: 'sig-2' });
    api.fetchPendingPermissions.mockResolvedValue({ unchanged: true });
    const sub = track();
    await flush();
    await permissionStore.refresh();
    await permissionStore.refresh();
    expect(sub.last().list.map((e) => e.request_id)).toEqual(['r2']);
    expect(api.fetchPendingPermissions.mock.calls.at(-1)[0]).toBe('sig-2');
    sub.unsubscribe();
    permissionStore.resolve('r2');
  });

  test('a reset forgets the signature with the list', async () => {
    api.fetchPendingPermissions.mockResolvedValue({ pending: [], signature: 'sig-1' });
    const sub = track();
    await flush();
    sub.unsubscribe();
    permissionStore.__resetForTests();
    api.fetchPendingPermissions.mockClear();
    const again = track();
    await flush();
    expect(api.fetchPendingPermissions.mock.calls[0][0]).toBe('');
    again.unsubscribe();
  });

  test('push is ignored for an already-resolved ask', async () => {
    const sub = track();
    await flush();
    permissionStore.resolve('r-done'); // tombstone first
    permissionStore.push('T1', _ask('T1', 'r-done'));
    expect(sub.last().list).toHaveLength(0);
    sub.unsubscribe();
  });

  test('audit sink routes a bubble to the registered task', () => {
    const sink = vi.fn();
    const off = permissionStore.registerAuditSink('T1', sink);
    permissionStore.emitAudit('T1', { text: 'ok' });
    expect(sink).toHaveBeenCalledWith({ text: 'ok' });
    // Unknown task → no throw, no call.
    permissionStore.emitAudit('T2', { text: 'x' });
    expect(sink).toHaveBeenCalledTimes(1);
    off();
    permissionStore.emitAudit('T1', { text: 'after-off' });
    expect(sink).toHaveBeenCalledTimes(1);
  });
});
