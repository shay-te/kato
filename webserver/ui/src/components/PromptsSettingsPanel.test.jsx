// Tests for the Prompts settings tab — edit/reset the predefined prompts
// through the shared promptStore.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../stores/toastStore.js', () => ({ toast: { show: vi.fn() } }));

import { promptStore } from '../stores/promptStore.js';
import PromptsSettingsPanel from './PromptsSettingsPanel.jsx';


beforeEach(() => { promptStore.reset('codeReview'); });


describe('PromptsSettingsPanel', () => {

  // Query the badge by its class so the assertion can't collide with the
  // override text (which may itself contain "custom"/"default").
  const badge = (container) =>
    container.querySelector('.settings-drawer-source').textContent;

  test('shows the default prompt text and the "default" badge', () => {
    const { container } = render(<PromptsSettingsPanel />);
    expect(badge(container)).toBe('default');
    const textarea = screen.getByLabelText('Code review prompt');
    expect(textarea.value).toMatch(/CODE REVIEW/);
  });

  test('editing + Save persists the override and the button reads it', () => {
    const { container } = render(<PromptsSettingsPanel />);
    const textarea = screen.getByLabelText('Code review prompt');
    fireEvent.change(textarea, { target: { value: 'MY OWN REVIEW PROMPT' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(promptStore.get('codeReview')).toBe('MY OWN REVIEW PROMPT');
    expect(promptStore.isCustom('codeReview')).toBe(true);
    expect(badge(container)).toBe('custom');
  });

  test('Save is disabled until the text is edited', () => {
    render(<PromptsSettingsPanel />);
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Code review prompt'), {
      target: { value: 'changed' },
    });
    expect(screen.getByRole('button', { name: /^save$/i })).not.toBeDisabled();
  });

  test('Reset to default clears a saved override', () => {
    promptStore.setOverride('codeReview', 'SAVED PROMPT TEXT');
    const { container } = render(<PromptsSettingsPanel />);
    expect(badge(container)).toBe('custom');
    fireEvent.click(screen.getByRole('button', { name: /reset to default/i }));
    expect(promptStore.isCustom('codeReview')).toBe(false);
    expect(badge(container)).toBe('default');
  });

  test('Reset is disabled when already on the default', () => {
    render(<PromptsSettingsPanel />);
    expect(screen.getByRole('button', { name: /reset to default/i })).toBeDisabled();
  });
});
