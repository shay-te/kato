// Component tests for PermissionDecisionContainer — a thin renderer over
// PermissionModal. Remembered "Allow always"/"Deny always" decisions are
// resolved SERVER-SIDE before an ask ever reaches this component (see
// kato_core_lib/helpers/tool_decision_store.py + _maybe_auto_resolve_pending
// in kato_webserver/app.py) — so this component has no recall/auto-submit
// logic of its own. Its contract:
//
//   1. Renders the modal for any non-null ``pending``.
//   2. A manual decision forwards {requestId, allow, rationale, remember}
//      to onSubmit, and on success calls onDismiss + emits an audit bubble.
//   3. A FAILED submit (backend rejects, or onSubmit throws) keeps the
//      modal up so the operator can retry.
//   4. Renders nothing when pending is null.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';


function _pending(overrides = {}) {
  return {
    type: 'control_request',
    request_id: 'req-1',
    request: {
      request_id: 'req-1',
      tool_name: 'Edit',
      input: { file_path: '/wk/x.py' },
    },
    ...overrides,
  };
}


describe('PermissionDecisionContainer — rendering', () => {
  test('renders the modal for a pending ask', () => {
    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={vi.fn()}
        onAuditBubble={vi.fn()}
      />,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
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
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});


describe('PermissionDecisionContainer — manual decision', () => {
  test('Allow once submits {allow: true, remember: false}', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    const onDismiss = vi.fn();
    const onAuditBubble = vi.fn();

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={onDismiss}
        onSubmit={onSubmit}
        onAuditBubble={onAuditBubble}
      />,
    );

    screen.getByRole('button', { name: /allow once/i }).click();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const call = onSubmit.mock.calls[0][0];
    expect(call.requestId).toBe('req-1');
    expect(call.allow).toBe(true);
    expect(call.remember).toBe(false);

    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
    expect(onAuditBubble).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('approved') }),
    );
  });

  test('Allow always submits {allow: true, remember: true} and the bubble names the tool', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    const onAuditBubble = vi.fn();

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={onAuditBubble}
      />,
    );

    screen.getByRole('button', { name: /allow always/i }).click();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0].remember).toBe(true);
    await waitFor(() => expect(onAuditBubble).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining('remembered for Edit') }),
    ));
  });

  test('Deny submits {allow: false}', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
      />,
    );

    screen.getByRole('button', { name: /^deny$/i }).click();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0].allow).toBe(false);
  });

  test('a FAILED submit keeps the modal up (operator can retry)', async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    const onDismiss = vi.fn();

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={onDismiss}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
      />,
    );

    screen.getByRole('button', { name: /allow once/i }).click();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onDismiss).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('a THROWN error in onSubmit is treated as a failure (modal stays up)', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('network down'));

    render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={onSubmit}
        onAuditBubble={vi.fn()}
      />,
    );

    screen.getByRole('button', { name: /allow once/i }).click();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});


describe('PermissionDecisionContainer — out-of-sandbox / high-risk still surface (via PermissionModal)', () => {
  test('an out-of-sandbox ask never offers "Allow always"', () => {
    render(
      <PermissionDecisionContainer
        pending={_pending({
          outside_sandbox: true,
          outside_path: '/etc/passwd',
          request: { request_id: 'req-1', tool_name: 'Edit', input: { file_path: '/etc/passwd' } },
        })}
        onDismiss={vi.fn()}
        onSubmit={vi.fn()}
        onAuditBubble={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
  });

  test('a high-risk Action Guard ask never offers "Allow always"', () => {
    render(
      <PermissionDecisionContainer
        pending={_pending({
          request: { request_id: 'req-1', tool_name: 'Bash', input: { command: 'cat ~/.ssh/id_rsa' } },
          action_guard: { category: 'credential_read', decision: 'block' },
        })}
        onDismiss={vi.fn()}
        onSubmit={vi.fn()}
        onAuditBubble={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
  });


  async function captureAuditBubble(allow) {
    const onAuditBubble = vi.fn();
    const { unmount } = render(
      <PermissionDecisionContainer
        pending={_pending()}
        onDismiss={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(true)}
        onAuditBubble={onAuditBubble}
      />,
    );
    screen.getByRole(
      'button', { name: allow ? /allow once/i : /^deny$/i },
    ).click();
    await waitFor(() => expect(onAuditBubble).toHaveBeenCalled());
    const entry = onAuditBubble.mock.calls[0][0];
    unmount();
    return entry;
  }
  // -------------------------------------------------------------------
  // Approve and deny must not look the same.
  //
  // Both outcomes are recorded as `system` bubbles, so they rendered in
  // the same neutral colour and the ✓ / ✗ glyph was the only difference —
  // "he shows the same color if I approve or deny". A tone modifier gives
  // the two decisions distinct dots.
  // -------------------------------------------------------------------

  test('an approval and a denial carry DIFFERENT tones', async () => {
    const approved = await captureAuditBubble(true);
    const denied = await captureAuditBubble(false);
    expect(approved.tone).toBeTruthy();
    expect(denied.tone).toBeTruthy();
    expect(approved.tone).not.toBe(denied.tone);
  });

  test('the tone names the outcome rather than a colour', async () => {
    // Colour belongs in the stylesheet; the entry records WHAT happened so
    // the two can be restyled without touching this file.
    expect((await captureAuditBubble(true)).tone).toBe('is-approved');
    expect((await captureAuditBubble(false)).tone).toBe('is-denied');
  });
});
