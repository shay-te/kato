// The commands menu only offers commands that actually work over kato's
// stream-json connection to the CLI — see constants/claudeCommands.js for the
// verified list. `/clear` destroys the conversation, so it confirms first.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import ComposerCommandMenu from './ComposerCommandMenu.jsx';
import { CLAUDE_COMMANDS } from '../constants/claudeCommands.js';

function open(props = {}) {
  const onRun = vi.fn();
  render(<ComposerCommandMenu onRun={onRun} {...props} />);
  fireEvent.click(screen.getByRole('button', { name: /claude commands/i }));
  return onRun;
}

describe('ComposerCommandMenu', () => {
  test('closed until asked for', () => {
    render(<ComposerCommandMenu onRun={vi.fn()} />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  test('lists every verified command', () => {
    open();
    for (const entry of CLAUDE_COMMANDS) {
      expect(screen.getByText(entry.command)).toBeInTheDocument();
    }
  });

  test('offers no command the transport rejects', () => {
    // These answer "isn't available in this environment" over stream-json;
    // listing them would read as kato being broken.
    open();
    for (const dead of ['/help', '/model', '/status', '/memory', '/mcp',
                        '/agents', '/rewind', '/config', '/doctor']) {
      expect(screen.queryByText(dead)).toBeNull();
    }
  });

  test('picking a safe command sends it immediately', () => {
    const onRun = open();
    fireEvent.click(screen.getByText('/compact'));
    expect(onRun).toHaveBeenCalledWith('/compact');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  test('/clear needs a second click before it runs', () => {
    // It erases the conversation the operator has been building — the same
    // loss an unwanted session restart would cause.
    const onRun = open();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.getByText(/click again to confirm/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).toHaveBeenCalledWith('/clear');
  });

  test('closing the menu disarms a pending /clear confirmation', () => {
    // Otherwise reopening later would leave it one click from wiping history.
    const onRun = open();
    fireEvent.click(screen.getByText('/clear'));
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /claude commands/i }));
    expect(screen.queryByText(/click again to confirm/i)).toBeNull();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).not.toHaveBeenCalled();
  });

  test('disabled while the composer is disabled', () => {
    render(<ComposerCommandMenu onRun={vi.fn()} disabled />);
    expect(screen.getByRole('button', { name: /claude commands/i })).toBeDisabled();
  });
});
