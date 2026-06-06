// Tests for the Claude-permissions settings panel — lists remembered
// tool decisions and re-scopes / clears them through the shared store,
// instantly (no save cycle).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';

vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import { toolDecisionsStore } from '../stores/toolDecisionsStore.js';
import ClaudePermissionsSettingsPanel from './ClaudePermissionsSettingsPanel.jsx';


beforeEach(() => { toolDecisionsStore.forget(); });


describe('ClaudePermissionsSettingsPanel', () => {

  test('empty state when no decisions are remembered', () => {
    render(<ClaudePermissionsSettingsPanel />);
    expect(screen.getByText(/no saved permissions yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('lists remembered tools name-sorted with their scope', () => {
    toolDecisionsStore.setDecision('Write', 'allow');
    toolDecisionsStore.setDecision('Bash', 'deny');
    render(<ClaudePermissionsSettingsPanel />);
    const rows = screen.getAllByRole('row').slice(1); // drop header
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Bash')).toBeInTheDocument();
    expect(within(rows[0]).getByLabelText('Scope for Bash')).toHaveValue('deny');
    expect(within(rows[1]).getByText('Write')).toBeInTheDocument();
    expect(within(rows[1]).getByLabelText('Scope for Write')).toHaveValue('allow');
  });

  test('changing the scope select persists through the store immediately', () => {
    toolDecisionsStore.setDecision('Bash', 'allow');
    render(<ClaudePermissionsSettingsPanel />);
    fireEvent.change(screen.getByLabelText('Scope for Bash'), {
      target: { value: 'deny' },
    });
    expect(toolDecisionsStore.recall('Bash')).toBe('deny');
  });

  test('Clear removes a single tool and the row disappears', () => {
    toolDecisionsStore.setDecision('Bash', 'allow');
    toolDecisionsStore.setDecision('Edit', 'allow');
    render(<ClaudePermissionsSettingsPanel />);

    const bashRow = screen.getByText('Bash').closest('tr');
    fireEvent.click(within(bashRow).getByRole('button', { name: /clear/i }));

    expect(toolDecisionsStore.recall('Bash')).toBeNull();
    expect(toolDecisionsStore.recall('Edit')).toBe('allow');
    expect(screen.queryByText('Bash')).toBeNull();
    expect(screen.getByText('Edit')).toBeInTheDocument();
  });

  test('Clear all wipes every decision and shows the empty state', () => {
    toolDecisionsStore.setDecision('Bash', 'allow');
    toolDecisionsStore.setDecision('Edit', 'deny');
    render(<ClaudePermissionsSettingsPanel />);

    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));

    expect(toolDecisionsStore.entries()).toEqual([]);
    expect(screen.getByText(/no saved permissions yet/i)).toBeInTheDocument();
  });

  test('a decision granted from elsewhere (store) appears without a reload', () => {
    render(<ClaudePermissionsSettingsPanel />);
    expect(screen.queryByText('Bash')).toBeNull();
    // Simulate a live prompt remembering a decision — the panel
    // subscribes to the store, so it re-renders without a reload.
    act(() => { toolDecisionsStore.setDecision('Bash', 'allow'); });
    expect(screen.getByText('Bash')).toBeInTheDocument();
  });
});
