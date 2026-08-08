// The modes menu replaces the standalone Plan-mode toggle: plan is one of
// four modes, and the trigger must always name the one that will actually run.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import ComposerModeMenu from './ComposerModeMenu.jsx';
import { AGENT_MODES } from '../constants/agentModes.js';

function open(props = {}) {
  const onChange = vi.fn();
  render(<ComposerModeMenu mode="" onChange={onChange} {...props} />);
  fireEvent.click(screen.getByRole('button', { name: /agent mode:/i }));
  return onChange;
}

describe('ComposerModeMenu', () => {
  test('the trigger names the active mode, never a generic label', () => {
    // "Mode" alone is how someone sends an edit believing it needed approval.
    render(<ComposerModeMenu mode="plan" onChange={vi.fn()} />);
    expect(
      screen.getByRole('button', { name: /agent mode: plan/i }),
    ).toBeInTheDocument();
  });

  test('an unknown stored mode falls back to the default, not a blank', () => {
    render(<ComposerModeMenu mode="somethingElse" onChange={vi.fn()} />);
    expect(
      screen.getByRole('button', { name: /agent mode: edit automatically/i }),
    ).toBeInTheDocument();
  });

  test('offers every mode with the active one checked', () => {
    open({ mode: 'plan' });
    const items = screen.getAllByRole('menuitemradio');
    expect(items).toHaveLength(AGENT_MODES.length);
    const checked = items.filter((i) => i.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveTextContent(/plan/i);
  });

  test('picking a different mode reports the CLI permission-mode value', () => {
    const onChange = open();
    fireEvent.click(screen.getByRole('menuitemradio', { name: /approve everything/i }));
    expect(onChange).toHaveBeenCalledWith('bypassPermissions');
  });

  test('the default mode reports an empty string, not a made-up value', () => {
    // '' means "kato's configured default"; inventing 'acceptEdits' here
    // would pin the task to a literal the operator never chose.
    const onChange = open({ mode: 'plan' });
    fireEvent.click(screen.getByRole('menuitemradio', { name: /edit automatically/i }));
    expect(onChange).toHaveBeenCalledWith('');
  });

  test('re-picking the active mode changes nothing', () => {
    // Each change re-spawns the subprocess to re-bake the flag; a no-op click
    // must not interrupt the agent.
    const onChange = open({ mode: 'plan' });
    fireEvent.click(screen.getByRole('menuitemradio', { name: /plan/i }));
    expect(onChange).not.toHaveBeenCalled();
  });

  test('Escape closes without choosing', () => {
    const onChange = open();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  test('disabled while the composer is disabled', () => {
    render(<ComposerModeMenu mode="" onChange={vi.fn()} disabled />);
    expect(screen.getByRole('button', { name: /agent mode:/i })).toBeDisabled();
  });
});
