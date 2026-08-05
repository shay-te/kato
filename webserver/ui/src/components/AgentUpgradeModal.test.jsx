import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import AgentUpgradeModal from './AgentUpgradeModal.jsx';

const CMD = 'npm install -g @anthropic-ai/claude-code@latest';

function renderModal(overrides = {}) {
  const props = {
    command: CMD,
    progress: null,
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<AgentUpgradeModal {...props} />) };
}

const RUNNING = {
  state: 'running',
  percent: 42,
  step: 'Downloading…',
  command: CMD,
  lines: ['npm http fetch GET 200 https://registry.npmjs.org/x'],
};

describe('AgentUpgradeModal', () => {
  test('renders as a dialog showing the exact command', () => {
    renderModal();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent(CMD);
  });

  test('Confirm fires onConfirm; Cancel fires onCancel', () => {
    const { props } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    expect(props.onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  test('while running: buttons disabled and the confirm shows progress', () => {
    const { props } = renderModal({ progress: RUNNING });
    const confirm = screen.getByRole('button', { name: /upgrading/i });
    expect(confirm).toBeDisabled();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  test('while running: a determinate progress bar reports the percentage', () => {
    renderModal({ progress: RUNNING });
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.getByText('Downloading…')).toBeInTheDocument();
  });

  test('while running: the host command output is shown', () => {
    renderModal({ progress: RUNNING });
    expect(screen.getByLabelText('Upgrade output')).toHaveTextContent(
      'npm http fetch GET 200',
    );
  });

  test('out-of-range or missing percentages are clamped, never NaN', () => {
    const { unmount } = renderModal({ progress: { ...RUNNING, percent: 250 } });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    unmount();
    renderModal({ progress: { ...RUNNING, percent: undefined } });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
  });

  test('on success: reports the outcome and offers only Close', () => {
    renderModal({
      progress: {
        state: 'done', ok: true, percent: 100,
        message: 'upgraded (2.1.179 → 2.1.222)', lines: ['added 1 package'],
      },
    });
    expect(screen.getByRole('dialog')).toHaveTextContent('Agent CLI upgraded');
    expect(screen.getByText(/2\.1\.179 → 2\.1\.222/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm upgrade/i })).toBeNull();
  });

  test('on failure: keeps the output so the error can be read back', () => {
    renderModal({
      progress: {
        state: 'error', ok: false, percent: 60,
        message: 'npm exited with code 1', lines: ['npm ERR! EACCES denied'],
      },
    });
    expect(screen.getByRole('dialog')).toHaveTextContent('Upgrade failed');
    expect(screen.getByText('npm exited with code 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Upgrade output')).toHaveTextContent('EACCES');
  });

  test('Close after a finished run fires onCancel', () => {
    const { props } = renderModal({
      progress: { state: 'done', ok: true, percent: 100, message: 'ok', lines: [] },
    });
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });
});
