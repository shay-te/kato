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

  test('Explain is offered, and reports its kato-level token', () => {
    // 'explain' is NOT a CLI --permission-mode: the spawn path resolves it
    // into one plus a read-only tool split. The picker must send the token.
    const onChange = open();
    fireEvent.click(screen.getByRole('menuitemradio', { name: /answer questions about the code/i }));
    expect(onChange).toHaveBeenCalledWith('explain');
  });

  test('Explain reads as distinct from Plan in the menu', () => {
    open({ mode: 'explain' });
    const explain = screen.getByRole('menuitemradio', { name: /explain/i });
    // The whole point of the mode: no edits AND no plan.
    expect(explain).toHaveTextContent(/no edits/i);
    expect(explain).toHaveTextContent(/no plan/i);
    expect(explain.getAttribute('aria-checked')).toBe('true');
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
    // Matched on Plan's own description: Explain's says "no plan", so a bare
    // /plan/i now hits two items. (The accessible name is label+description
    // concatenated, so anchoring on /^Plan/ can't disambiguate either.)
    fireEvent.click(screen.getByRole('menuitemradio', {
      name: /never edits or runs mutating tools/i,
    }));
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

  test('hosts ultracode so it does not need its own toolbar pill', () => {
    // The toolbar overflowed and drew controls on top of each other on a
    // narrow window; the rarely-touched toggles moved in here.
    const onUltracodeChange = vi.fn();
    render(
      <ComposerModeMenu
        mode=""
        onChange={vi.fn()}
        supportsWorkflows
        ultracode={false}
        onUltracodeChange={onUltracodeChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /agent mode:/i }));
    const toggle = screen.getByRole('menuitemcheckbox', { name: /ultracode/i });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(toggle);
    expect(onUltracodeChange).toHaveBeenCalledWith(true);
  });

  test('hides ultracode when the CLI cannot run workflows', () => {
    render(<ComposerModeMenu mode="" onChange={vi.fn()} supportsWorkflows={false} />);
    fireEvent.click(screen.getByRole('button', { name: /agent mode:/i }));
    expect(screen.queryByRole('menuitemcheckbox', { name: /ultracode/i })).toBeNull();
  });

  test('View plan only appears when there is a plan, and closes the menu', () => {
    const onOpenPlan = vi.fn();
    const { rerender } = render(
      <ComposerModeMenu mode="" onChange={vi.fn()} planAvailable={false} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /agent mode:/i }));
    expect(screen.queryByRole('menuitem', { name: /view plan/i })).toBeNull();

    rerender(
      <ComposerModeMenu
        mode="" onChange={vi.fn()} planAvailable onOpenPlan={onOpenPlan}
      />,
    );
    fireEvent.click(screen.getByRole('menuitem', { name: /view plan/i }));
    expect(onOpenPlan).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
