// Component tests for PermissionDecisionContainer — the surface that
// auto-handles "remembered" tool decisions and renders the modal
// otherwise. Three operator-trust contracts:
//
//   1. When the tool has a remembered "allow" / "deny" decision,
//      auto-submit silently (no modal flash).
//   2. When auto-submit FAILS (Bug C territory: backend rejects),
//      the modal MUST resurface so the operator can retry or
//      decide manually. The "auto-failed" record_id is tracked to
//      prevent an infinite auto-retry loop.
//   3. Manual decisions call ``rememberToolDecision`` only when
//      the operator ticks "remember", and emit a system bubble
//      describing what happened.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';

import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';


function _pending(overrides = {}) {
  return {
    type: 'control_request',
    request_id: 'req-1',
    request: {
      request_id: 'req-1',
      tool_name: 'Bash',
      input: { command: 'ls' },
    },
    ...overrides,
  };
}


describe('PermissionDecisionContainer — auto-allow / auto-deny', () => {

  test('auto-submits "allow" when the tool has a remembered allow decision', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    const onDismiss = vi.fn();
    const onAuditBubble = vi.fn();

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={onDismiss}
        onSubmit={onSubmit}
        onAuditBubble={onAuditBubble}
        recallToolDecision={(tool) => (tool === 'Bash' ? 'allow' : null)}
        rememberToolDecision={vi.fn()}
      />,
    );

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());

    const call = onSubmit.mock.calls[0][0];
    expect(call.allow).toBe(true);
    expect(call.requestId).toBe('req-1');
    // Auto-submit must NOT request re-remembering (it's already
    // remembered — double-write is a waste).
    expect(call.remember).toBe(false);

    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
    // Audit bubble announces the auto-action so the operator can
    // scroll back and see what was approved without their input.
    expect(onAuditBubble).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('auto-allow'),
      }),
    );
  });

  test('auto-submits "deny" when the tool has a remembered deny decision', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => 'deny'}
        rememberToolDecision={vi.fn()}
      />,
    );

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0].allow).toBe(false);
  });

  test('does NOT render the modal while auto-submitting', () => {
    // The modal would flash on screen briefly; we hide it so the
    // operator only sees auto-handled tools as a silent audit bubble.
    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(true)}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => 'allow'}
        rememberToolDecision={vi.fn()}
      />,
    );
    // Modal hidden during auto-submit (no dialog role rendered).
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('auto-submit FAILURE resurfaces the modal for manual handling (Bug C surface)', async () => {
    // Critical regression guard: if the backend rejects the
    // remembered decision (e.g., stdin write failed — exactly the
    // Bug C scenario), the modal MUST come back so the operator
    // can retry instead of being silently stuck.
    const onSubmit = vi.fn().mockResolvedValue(false);  // backend rejection

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => 'allow'}
        rememberToolDecision={vi.fn()}
      />,
    );

    // After auto-submit settles, the modal should be visible.
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
    });
    // The auto-submit failed, so the modal renders.
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeInTheDocument();
    });
  });

  test('thrown error in onSubmit is treated as a failure (modal resurfaces)', async () => {
    // Belt-and-braces: even if onSubmit throws instead of
    // returning false, the container must catch and treat as
    // delivery failure. Otherwise the silent failure leaves the
    // operator with no UI.
    const onSubmit = vi.fn().mockRejectedValue(new Error('network down'));

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => 'allow'}
        rememberToolDecision={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeInTheDocument();
    });
  });
});


describe('PermissionDecisionContainer — no remembered decision', () => {

  test('renders the modal when nothing is remembered for the tool', () => {
    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={vi.fn()}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => null}
        rememberToolDecision={vi.fn()}
      />,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('button', { name: /allow once/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow always/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument();
  });

  test('renders nothing at all when no pending permission exists', () => {
    const { container } = render(
      <PermissionDecisionContainer
        pending={null}
        onDismiss={vi.fn()}
        onSubmit={vi.fn()}
        onAuditBubble={vi.fn()}
        recallToolDecision={() => null}
        rememberToolDecision={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
