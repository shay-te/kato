// Tests for usePushApproval — reads "kato is paused waiting for your push
// approval" off the session record and exposes an approve() action.
//
// It used to poll GET /api/sessions/<id>/awaiting-push-approval every 5s. That
// endpoint and the session record's ``has_changes_pending`` are the SAME
// server expression, and SessionHeader already receives the record. So the
// poll was not merely 12 requests a minute for a value already on the wire —
// it was two unsynchronised 5s timers over one boolean, and the Approve button
// could disagree with the tab's pending-changes row for up to five seconds.
//
// Contract now:
//   - awaiting mirrors session.has_changes_pending — no request of its own.
//   - approve() posts, and optimistically clears the button on success only.
//   - the optimistic clear is dropped once the server confirms, and on a task
//     switch, so a LATER pending push shows the button again.
//   - double-click guard via the busy flag.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  approveTaskPush: vi.fn(),
  // Deliberately still mocked: if anything reintroduces a poll here, the
  // "issues no request of its own" test below fails loudly rather than
  // silently passing against a module that no longer exports it.
  fetchAwaitingPushApproval: vi.fn(),
}));

import { approveTaskPush, fetchAwaitingPushApproval } from '../api.js';
import { usePushApproval } from './usePushApproval.js';

const session = (extra = {}) => ({ task_id: 'T1', ...extra });

beforeEach(() => {
  approveTaskPush.mockReset();
  fetchAwaitingPushApproval.mockReset();
  approveTaskPush.mockResolvedValue({ ok: true });
});


describe('usePushApproval — reads the record, issues no poll', () => {
  test('no request of its own, ever', async () => {
    renderHook(() => usePushApproval(session({ has_changes_pending: true })));
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchAwaitingPushApproval).not.toHaveBeenCalled();
  });

  test('awaiting mirrors has_changes_pending', () => {
    const { result: on } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    expect(on.current.awaiting).toBe(true);

    const { result: off } = renderHook(
      () => usePushApproval(session({ has_changes_pending: false })),
    );
    expect(off.current.awaiting).toBe(false);
  });

  test('a record that arrives later flips the button on with no fetch', () => {
    // The value rides the 5s /api/sessions poll the app already runs.
    const { result, rerender } = renderHook(
      ({ s }) => usePushApproval(s),
      { initialProps: { s: session({ has_changes_pending: false }) } },
    );
    expect(result.current.awaiting).toBe(false);

    rerender({ s: session({ has_changes_pending: true }) });
    expect(result.current.awaiting).toBe(true);
    expect(fetchAwaitingPushApproval).not.toHaveBeenCalled();
  });

  test('no session means nothing to approve', async () => {
    const { result } = renderHook(() => usePushApproval(null));
    expect(result.current.awaiting).toBe(false);
    let out;
    await act(async () => { out = await result.current.approve(); });
    expect(out).toBeNull();
    expect(approveTaskPush).not.toHaveBeenCalled();
  });
});


describe('usePushApproval — approving', () => {
  test('posts for the record it was given', async () => {
    const { result } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    await act(async () => { await result.current.approve(); });
    expect(approveTaskPush).toHaveBeenCalledWith('T1');
  });

  test('clears the button immediately on success', async () => {
    // The record still says pending until the next /api/sessions tick. Without
    // the optimistic clear the operator clicks Approve and the button sits
    // there for up to 5s looking like the click missed.
    const { result } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    await act(async () => { await result.current.approve(); });
    expect(result.current.awaiting).toBe(false);
  });

  test('a FAILED approve leaves the button up', async () => {
    // Hiding it would strand the operator with no way to retry and no sign why.
    approveTaskPush.mockResolvedValue({ ok: false, error: 'no pending publish' });
    const { result } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    await act(async () => { await result.current.approve(); });
    expect(result.current.awaiting).toBe(true);
  });

  test('busy is set while the post is in flight', async () => {
    let release;
    approveTaskPush.mockReturnValue(new Promise((r) => { release = r; }));
    const { result } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    act(() => { result.current.approve(); });
    await waitFor(() => expect(result.current.busy).toBe(true));

    await act(async () => { release({ ok: true }); });
    await waitFor(() => expect(result.current.busy).toBe(false));
  });

  test('a second click while busy is a no-op', async () => {
    let release;
    approveTaskPush.mockReturnValue(new Promise((r) => { release = r; }));
    const { result } = renderHook(
      () => usePushApproval(session({ has_changes_pending: true })),
    );
    act(() => { result.current.approve(); });
    await waitFor(() => expect(result.current.busy).toBe(true));

    let second;
    await act(async () => { second = await result.current.approve(); });
    expect(second).toBeNull();
    expect(approveTaskPush).toHaveBeenCalledTimes(1);

    await act(async () => { release({ ok: true }); });
  });
});


describe('usePushApproval — the optimistic clear does not stick', () => {
  test('a LATER pending push shows the button again', async () => {
    // Approve, the server catches up (flag down), then new commits land. The
    // dismissal must not survive that round trip or the operator can never
    // approve a second push.
    const { result, rerender } = renderHook(
      ({ s }) => usePushApproval(s),
      { initialProps: { s: session({ has_changes_pending: true }) } },
    );
    await act(async () => { await result.current.approve(); });
    expect(result.current.awaiting).toBe(false);

    rerender({ s: session({ has_changes_pending: false }) }); // server caught up
    rerender({ s: session({ has_changes_pending: true }) });  // new commits
    expect(result.current.awaiting).toBe(true);
  });

  test('switching task drops the previous task\'s dismissal', async () => {
    const { result, rerender } = renderHook(
      ({ s }) => usePushApproval(s),
      { initialProps: { s: session({ has_changes_pending: true }) } },
    );
    await act(async () => { await result.current.approve(); });
    expect(result.current.awaiting).toBe(false);

    rerender({ s: { task_id: 'T2', has_changes_pending: true } });
    expect(result.current.awaiting).toBe(true);
  });

  test('it holds while the server has not caught up yet', async () => {
    // The whole point: the record still reports pending for up to 5s after a
    // successful approve, and the button must stay down through that.
    const { result, rerender } = renderHook(
      ({ s }) => usePushApproval(s),
      { initialProps: { s: session({ has_changes_pending: true }) } },
    );
    await act(async () => { await result.current.approve(); });

    rerender({ s: session({ has_changes_pending: true }) }); // stale poll
    expect(result.current.awaiting).toBe(false);
  });
});


// The ordering that used to strand the operator.
//
// approve_push pops the pending entry BEFORE running the push the HTTP request
// is still blocked on, and the 5s session poll is served mid-publish. So the
// server's flag can fall — and rise again when the task re-parks — while the
// POST is still in flight. Clearing the optimistic dismissal only on the
// FALLING edge meant it never cleared again: the Approve button was gone for
// the rest of the tab's life, with the tab row still reporting unpushed work
// and no control left to resume the parked publish.
describe('usePushApproval — the server confirming before the POST resolves', () => {
  test('a re-parked task shows the button again', async () => {
    let release;
    approveTaskPush.mockReturnValue(new Promise((r) => { release = r; }));

    const { result, rerender } = renderHook(
      ({ s }) => usePushApproval(s),
      { initialProps: { s: session({ has_changes_pending: true }) } },
    );

    act(() => { result.current.approve(); });
    await waitFor(() => expect(result.current.busy).toBe(true));

    // The poll catches the flag going down mid-publish.
    rerender({ s: session({ has_changes_pending: false }) });

    // ...and only THEN does the POST resolve, setting the optimistic clear.
    await act(async () => { release({ ok: true }); });

    // The task re-parks (NO_CHANGES, a failed push, the next scan tick).
    rerender({ s: session({ has_changes_pending: true }) });

    expect(result.current.awaiting).toBe(true);
  });
});
