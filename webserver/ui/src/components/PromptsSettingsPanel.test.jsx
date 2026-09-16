// Tests for the Prompts settings tab — edit/reset the predefined prompts
// through the shared promptStore.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../stores/toastStore.js', () => ({ toast: { show: vi.fn() } }));

import { promptStore } from '../stores/promptStore.js';
import PromptsSettingsPanel from './PromptsSettingsPanel.jsx';


beforeEach(() => {
  promptStore.reset('codeReview');
  for (const prompt of promptStore.list()) {
    if (!prompt.builtin) { promptStore.remove(prompt.id); }
  }
});

const review = () => promptStore.list().find((p) => p.id === 'codeReview');

// Opens a prompt's collapsed row by its name.
function openPrompt(name) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}`, 'i'), expanded: false }));
}


describe('PromptsSettingsPanel', () => {

  // Query the badge by its class so the assertion can't collide with the
  // override text (which may itself contain "custom"/"default").
  const badge = (container) =>
    container.querySelector('.settings-drawer-source').textContent;

  test('each prompt starts collapsed to a single row', () => {
    render(<PromptsSettingsPanel />);
    expect(screen.getByRole('button', { name: /^code review/i, expanded: false })).toBeInTheDocument();
    expect(screen.queryByLabelText('Code review prompt')).toBeNull();
    openPrompt('code review');
    expect(screen.getByLabelText('Code review prompt')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^code review/i, expanded: true }));
    expect(screen.queryByLabelText('Code review prompt')).toBeNull();
  });

  test('shows the default prompt text and the "default" badge', () => {
    const { container } = render(<PromptsSettingsPanel />);
    expect(badge(container)).toBe('default');
    openPrompt('code review');
    const textarea = screen.getByLabelText('Code review prompt');
    expect(textarea.value).toMatch(/CODE REVIEW/);
  });

  test('editing + Save persists the change and the button reads it', () => {
    const { container } = render(<PromptsSettingsPanel />);
    openPrompt('code review');
    const textarea = screen.getByLabelText('Code review prompt');
    fireEvent.change(textarea, { target: { value: 'MY OWN REVIEW PROMPT' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(promptStore.get('codeReview')).toBe('MY OWN REVIEW PROMPT');
    expect(review().changed).toBe(true);
    expect(badge(container)).toBe('custom');
  });

  test('Save is disabled until the text is edited', () => {
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Code review prompt'), {
      target: { value: 'changed' },
    });
    expect(screen.getByRole('button', { name: /^save$/i })).not.toBeDisabled();
  });

  test('picking an icon applies it immediately — no Save needed', () => {
    // "still i can't change the prompt icon": the click only updated the
    // draft, so nothing happened unless Save was pressed afterwards.
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    fireEvent.click(screen.getByRole('radio', { name: 'eye' }));
    expect(screen.getByRole('radio', { name: 'eye' })).toHaveAttribute('aria-checked', 'true');
    expect(review().icon).toBe('eye');
  });

  test('the name still waits for Save', () => {
    // A half-typed name must not reach the toolbar on every keystroke.
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    fireEvent.change(screen.getByLabelText('Code review name'), { target: { value: 'Review' } });
    expect(review().label).toBe('Code review');
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(review()).toMatchObject({ label: 'Review' });
  });

  test('a blank name cannot be saved', () => {
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    fireEvent.change(screen.getByLabelText('Code review name'), { target: { value: '  ' } });
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });

  test('Reset to default clears a saved change', () => {
    promptStore.save('codeReview', { label: 'Code review', icon: 'diff', text: 'SAVED PROMPT TEXT' });
    const { container } = render(<PromptsSettingsPanel />);
    expect(badge(container)).toBe('custom');
    openPrompt('code review');
    fireEvent.click(screen.getByRole('button', { name: /reset to default/i }));
    expect(review().changed).toBe(false);
    expect(badge(container)).toBe('default');
  });

  test('Reset is disabled when already on the default', () => {
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    expect(screen.getByRole('button', { name: /reset to default/i })).toBeDisabled();
  });

  test('a shipped prompt cannot be deleted', () => {
    render(<PromptsSettingsPanel />);
    openPrompt('code review');
    expect(screen.queryByRole('button', { name: /^delete$/i })).toBeNull();
  });
});


describe('PromptsSettingsPanel — adding prompts', () => {
  function fillNewPrompt({ name, text }) {
    fireEvent.click(screen.getByRole('button', { name: /^add prompt$/i }));
    fireEvent.change(screen.getByLabelText('New name'), { target: { value: name } });
    fireEvent.change(screen.getByLabelText('New prompt'), { target: { value: text } });
  }

  test('Add puts the prompt in the list', () => {
    render(<PromptsSettingsPanel />);
    fillNewPrompt({ name: 'Explain the diff', text: 'Explain what changed.' });
    fireEvent.click(screen.getByRole('radio', { name: 'eye' }));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    const added = promptStore.list().find((p) => !p.builtin);
    expect(added).toMatchObject({ label: 'Explain the diff', icon: 'eye', text: 'Explain what changed.' });
    expect(screen.getByRole('button', { name: /^explain the diff/i, expanded: false })).toHaveTextContent('added');
    expect(screen.getByRole('button', { name: /^add prompt$/i })).toBeInTheDocument();
  });

  test('Add is disabled until the name and the text are both filled', () => {
    render(<PromptsSettingsPanel />);
    fillNewPrompt({ name: 'Explain', text: '' });
    expect(screen.getByRole('button', { name: /^add$/i })).toBeDisabled();
  });

  test('Cancel adds nothing', () => {
    render(<PromptsSettingsPanel />);
    fillNewPrompt({ name: 'Explain', text: 'Explain.' });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(promptStore.list()).toHaveLength(1);
    expect(screen.queryByLabelText('New prompt')).toBeNull();
  });

  test('Delete removes an added prompt', () => {
    promptStore.add({ label: 'Go', icon: 'send', text: 'Go ahead.' });
    render(<PromptsSettingsPanel />);
    openPrompt('go');
    fireEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    expect(promptStore.list().map((p) => p.id)).toEqual(['codeReview']);
    expect(screen.queryByRole('button', { name: /^go/i })).toBeNull();
  });
});
