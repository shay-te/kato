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
      tool_name: 'Edit',
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
    expect(screen.getByText('Edit')).toBeInTheDocument();
  });

  test('default title when no task_id (focused-task ask)', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    expect(screen.getByText('Approval requested')).toBeInTheDocument();
  });

  test('titles with the task code for a cross-task ask (task_id stamped)', () => {
    render(<PermissionModal raw={_raw({ task_id: 'POJ-2' })} onDecide={vi.fn()} />);
    expect(screen.getByRole('heading')).toHaveTextContent(/POJ-2.*wants permission/);
    // The task code is its own bold element.
    expect(screen.getByText('POJ-2')).toHaveClass('permission-modal-task');
    expect(screen.queryByText('Approval requested')).toBeNull();
  });

  test('taskCode prop titles the focused-task modal (SSE envelope has no task_id)', () => {
    render(<PermissionModal raw={_raw()} onDecide={vi.fn()} taskCode="POJ-1" />);
    expect(screen.getByRole('heading')).toHaveTextContent(/POJ-1.*wants permission/);
    expect(screen.getByText('POJ-1')).toHaveClass('permission-modal-task');
  });

  test('title shows the task summary on the same line as the code', () => {
    render(
      <PermissionModal
        raw={_raw()}
        onDecide={vi.fn()}
        taskCode="POJ-1"
        taskSummary="add library collaborators"
      />,
    );
    const heading = screen.getByRole('heading');
    expect(heading).toHaveTextContent(/POJ-1.*add library collaborators/);
    expect(heading).toHaveTextContent(/wants permission/);
    expect(screen.getByText('add library collaborators')).toHaveClass(
      'permission-modal-title-summary',
    );
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

  test('out-of-sandbox ask shows the loud h1 warning + names the path', () => {
    const { container } = render(<PermissionModal raw={_raw({
      outside_sandbox: true,
      outside_path: '/etc/passwd',
      request: { request_id: 'req-1', tool_name: 'Edit', input: { file_path: '/etc/passwd' } },
    })} onDecide={vi.fn()} />);
    const warn = container.querySelector('#permission-outside-sandbox');
    expect(warn).toBeInTheDocument();
    // The title is an <h1> and reads in uppercase.
    const h1 = warn.querySelector('h1');
    expect(h1).toBeInTheDocument();
    expect(h1.textContent).toMatch(/OUTSIDE THE TASK FOLDER/);
    expect(warn.textContent).toMatch(/\/etc\/passwd/);
  });

  test('out-of-sandbox ask withholds the "Allow always" button', () => {
    render(<PermissionModal raw={_raw({
      outside_sandbox: true, outside_path: '/etc/x',
      request: { request_id: 'req-1', tool_name: 'Edit', input: { file_path: '/etc/x' } },
    })} onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /allow once/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
  });

  test('in-sandbox ask shows no warning and keeps "Allow always"', () => {
    const { container } = render(<PermissionModal raw={_raw()} onDecide={vi.fn()} />);
    expect(container.querySelector('#permission-outside-sandbox')).toBeNull();
    expect(screen.getByRole('button', { name: /allow always/i })).toBeInTheDocument();
  });

  test('Action Guard ask shows the risk banner (category + reason)', () => {
    const { container } = render(<PermissionModal raw={_raw({
      action_guard: {
        category: 'credential_read', decision: 'block',
        reason: 'accesses a credential / secret file',
      },
    })} onDecide={vi.fn()} />);
    const banner = container.querySelector('#permission-action-guard');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/CREDENTIAL READ/i);
    expect(banner).toHaveTextContent(/credential . secret file/i);
  });

  test('high-risk Action Guard category withholds "Allow always"', () => {
    render(<PermissionModal raw={_raw({
      action_guard: { category: 'credential_read', decision: 'block' },
    })} onDecide={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
    expect(screen.getByRole('button', { name: /allow once/i })).toBeInTheDocument();
  });

  test('dual-use Action Guard category (ask) keeps "Allow always"', () => {
    render(<PermissionModal raw={_raw({
      action_guard: { category: 'destructive_fs', decision: 'ask' },
    })} onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: /allow always/i })).toBeInTheDocument();
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

  test('an escaping command (backend-flagged outside_sandbox) shows the red warning + withholds Allow always', () => {
    // The backend flags docker/sudo/etc. as outside_sandbox; the modal then
    // reuses the out-of-task treatment (red banner, no remembered scope).
    const { container } = render(<PermissionModal raw={_raw({
      outside_sandbox: true,
      outside_path: 'docker',
      request: { request_id: 'r', tool_name: 'Bash', input: { command: 'docker run x' } },
    })} onDecide={vi.fn()} />);
    expect(container.querySelector('#permission-outside-sandbox')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
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
    expect(arg.toolName).toBe('Edit');
  });

  test('Allow once → allow=true, remember=false', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /allow once/i }));

    expect(onDecide.mock.calls[0][0]).toMatchObject({
      allow: true, remember: false, requestId: 'req-1', toolName: 'Edit',
    });
  });

  test('Allow always → allow=true, remember=true', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_raw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /allow always/i }));

    expect(onDecide.mock.calls[0][0]).toMatchObject({
      allow: true, remember: true, requestId: 'req-1', toolName: 'Edit',
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


describe('PermissionModal — AskUserQuestion', () => {
  function _askRaw() {
    return _raw({
      request_id: 'q1',
      request: {
        request_id: 'q1',
        tool_name: 'AskUserQuestion',
        input: {
          questions: [{
            question: 'How should the columns appear?',
            header: 'Column layout',
            multiSelect: false,
            options: [
              { label: 'Single Matchmaker column', description: 'Cleanest.' },
              { label: 'Separate Promiser + Executer', description: 'Two cols.' },
            ],
          }],
        },
      },
    });
  }

  test('renders the answer form (options + Send answer), not the allow/deny buttons', () => {
    render(<PermissionModal raw={_askRaw()} onDecide={vi.fn()} />);
    expect(screen.getByText('Single Matchmaker column')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send answer/i })).toBeInTheDocument();
    // The normal permission affordances are NOT shown for a question.
    expect(screen.queryByRole('button', { name: /allow once/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /allow always/i })).toBeNull();
  });

  test('sending an answer replies with the selection as the message (deny channel)', () => {
    const onDecide = vi.fn();
    render(<PermissionModal raw={_askRaw()} onDecide={onDecide} />);
    fireEvent.click(screen.getByText('Separate Promiser + Executer'));
    fireEvent.click(screen.getByRole('button', { name: /send answer/i }));
    expect(onDecide).toHaveBeenCalledTimes(1);
    const arg = onDecide.mock.calls[0][0];
    expect(arg.allow).toBe(false);
    expect(arg.requestId).toBe('q1');
    expect(arg.rationale).toContain('Separate Promiser + Executer');
  });

  test('is backend-agnostic: a non-AskUserQuestion tool with the questions shape still renders the form', () => {
    // Detection is by SHAPE, not name — so a different backend emitting the
    // same questions payload renders the answer form, not an allow/deny grant.
    const raw = _raw({
      request_id: 'q2',
      request: {
        request_id: 'q2',
        tool_name: 'SomeOtherBackendQuestionTool',
        input: {
          questions: [{
            question: 'Pick a region',
            options: [
              { label: 'us-east', description: 'Virginia' },
              { label: 'eu-west', description: 'Ireland' },
            ],
          }],
        },
      },
    });
    render(<PermissionModal raw={raw} onDecide={vi.fn()} />);
    expect(screen.getByText('us-east')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send answer/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow once/i })).toBeNull();
  });
});
