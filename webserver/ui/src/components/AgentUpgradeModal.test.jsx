import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import AgentUpgradeModal from './AgentUpgradeModal.jsx';

const CMD = 'npm install -g @anthropic-ai/claude-code@latest';

function renderModal(overrides = {}) {
  const props = {
    command: CMD,
    running: false,
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<AgentUpgradeModal {...props} />) };
}

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
    const { props } = renderModal({ running: true });
    const confirm = screen.getByRole('button', { name: /upgrading/i });
    expect(confirm).toBeDisabled();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirm).not.toHaveBeenCalled();
  });
});
