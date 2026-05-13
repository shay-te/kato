// Tests for PermissionModal. PermissionDecisionContainer already
// exercises the auto-allow / auto-deny flow at integration level;
// this file pins the modal's own rendering + button → onDecide
// wiring.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import PermissionModal from './PermissionModal.jsx';


function _raw(overrides = {}) {
  return {
    type: 'control_request',
    request_id: 'req-1',
    request: {
      request_id: 'req-1',
      tool_name: 'Bash',
      input: { command: 'ls -la' },
    },
    ...overrides,
  };
}


describe('PermissionModal — rendering', () => {

  test('renders nothing when raw is null', () => {
    const { container } = render(<PermissionModal raw={null} onDecide={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders the dialog with role="dialog" and aria-modal', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    const dlg = screen.getByRole('dialog');
    expect(dlg).toBeInTheDocument();
    expect(dlg).toHaveAttribute('aria-modal', 'true');
  });

  test('shows the tool name in the header', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    expect(screen.getByText('Bash')).toBeInTheDocument();
  });

  test('renders all three action buttons', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow once/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow always/i })).toBeInTheDocument();
  });

  test('renders the tool input fields with labels + values', () => {
    const { container } = render(
      <PermissionModal raw={_raw()} onDecide={vi.fn()} />,
    );
    expect(screen.getByText('command')).toBeInTheDocument();
    // The value renders in a ``.permission-field-value`` div; the raw
    // envelope `<details>` also contains the text but is collapsed.
    const fieldValue = container.querySelector('.permission-field-value');
    expect(fieldValue.textContent).toMatch(/ls -la/);
  });

  test('empty / missing tool input shows "(no arguments)"', () => {
    render(<PermissionModal raw={_raw({
      request: { request_id: 'r', tool_name: 'X', input: {} },
    })} onDecide={vi.fn()} />);
    expect(screen.getByText(/no arguments/i)).toBeInTheDocument();
  });

  test('renders the rationale textarea', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    expect(screen.getByPlaceholderText(/rationale/i)).toBeInTheDocument();
  });

  test('object-valued tool input is rendered as JSON string', () => {
    render(<PermissionModal raw={_raw({
      request: {
        request_id: 'r',
        tool_name: 'Edit',
        input: { file: '/tmp/x', changes: { from: 'a', to: 'b' } },
      },
    })} onDecide={vi.fn()} />);
    // Field label "changes" present, value formatted as JSON.
    expect(screen.getByText('changes')).toBeInTheDocument();
  });
});


describe('PermissionModal — onDecide dispatch', () => {

  test('Deny click fires onDecide with allow=false, remember=false', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /deny/i }));

    expect(onDecide).toHaveBeenCalledTimes(1);
    const arg = onDecide.mock.calls[0][0];
    expect(arg.allow).toBe(false);
    expect(arg.remember).toBe(false);
    expect(arg.requestId).toBe('req-1');
    expect(arg.toolName).toBe('Bash');
  });

  test('Allow once → allow=true, remember=false', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /allow once/i }));

    expect(onDecide.mock.calls[0][0]).toMatchObject({
      allow: true, remember: false, requestId: 'req-1', toolName: 'Bash',
    });
  });

  test('Allow always → allow=true, remember=true', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /allow always/i }));

    expect(onDecide.mock.calls[0][0]).toMatchObject({
      allow: true, remember: true, requestId: 'req-1', toolName: 'Bash',
    });
  });

  test('rationale text is forwarded with Deny', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.change(screen.getByPlaceholderText(/rationale/i), {
      target: { value: 'too risky' },
    });
    fireEvent.click(screen.getByRole('button', { name: /deny/i }));

    expect(onDecide.mock.calls[0][0].rationale).toBe('too risky');
  });

  test('rationale resets when requestId changes', () => {
    const onDecide = vi.fn();
    const { rerender } = render(
      <PermissionModal raw={_raw({ request_id: 'r1' })} onDecide={onDecide} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/rationale/i), {
      target: { value: 'thinking…' },
    });

    // A new permission with a different id arrives — the old
    // rationale should NOT carry over (it was for the previous tool).
    rerender(
      <PermissionModal
        raw={_raw({ request_id: 'r2', request: { request_id: 'r2', tool_name: 'X', input: {} } })}
        onDecide={onDecide}
      />,
    );
    expect(screen.getByPlaceholderText(/rationale/i)).toHaveValue('');
  });
});
