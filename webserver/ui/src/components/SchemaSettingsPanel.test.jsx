// Tests for SchemaField's datalist autocomplete — the OpenRouter model field
// pulls its suggestions live from the backend (same {models} shape as the chat
// model picker) instead of hardcoding slugs.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const fetchOpenRouterModels = vi.fn();
const fetchClaudeModels = vi.fn();

vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(() => Promise.resolve({ sections: [], values: {} })),
  updateAllSettings: vi.fn(() => Promise.resolve({})),
  fetchOpenRouterModels: (...args) => fetchOpenRouterModels(...args),
  fetchClaudeModels: (...args) => fetchClaudeModels(...args),
}));

import { SchemaField, resetDatalistCacheForTests, selectOptionLabel } from './SchemaSettingsPanel.jsx';

const OPENROUTER_FIELD = {
  key: 'OPENHANDS_LLM_MODEL',
  type: 'text',
  label: 'Model',
  placeholder: 'openrouter/openai/gpt-4o',
  datalist: 'openrouter',
};

describe('SchemaField — datalist autocomplete', () => {
  beforeEach(() => {
    fetchOpenRouterModels.mockReset();
    fetchClaudeModels.mockReset();
    resetDatalistCacheForTests();
  });

  test('a datalist field fetches live options and renders them as <option>s', async () => {
    fetchOpenRouterModels.mockResolvedValue([
      { id: 'openrouter/openai/gpt-4o', label: 'OpenAI: GPT-4o' },
      { id: 'openrouter/anthropic/claude-opus-4.8', label: 'Anthropic: Claude Opus 4.8' },
    ]);
    const { container } = render(
      <SchemaField field={OPENROUTER_FIELD} value="" onChange={() => {}} />,
    );
    // Input is wired to the datalist for browser-native autocomplete.
    const input = container.querySelector('input');
    expect(input.getAttribute('list')).toBe('datalist-OPENHANDS_LLM_MODEL');
    expect(input.getAttribute('placeholder')).toBe('openrouter/openai/gpt-4o');
    // Options arrive asynchronously from the live catalogue.
    await waitFor(() => {
      const options = container.querySelectorAll('datalist option');
      expect(options.length).toBe(2);
    });
    const values = [...container.querySelectorAll('datalist option')].map((o) => o.value);
    expect(values).toContain('openrouter/openai/gpt-4o');
    expect(fetchOpenRouterModels).toHaveBeenCalled();
  });

  // Report: "he works on an older model, Sonnet 4.6 — he types it and kato says
  // there is no such model". The model field is a dropdown of every supported
  // model, the older ones included, with "Other" for anything else.
  const CLAUDE_MODELS = [
    { id: 'sonnet', label: 'Sonnet 5.5 — always the newest Sonnet', group: 'newest' },
    { id: 'claude-sonnet-5-5', label: 'Sonnet 5.5', group: 'version' },
    { id: 'claude-sonnet-4-6', label: 'Sonnet 4.6', group: 'version' },
  ];
  const MODEL_FIELD = {
    key: 'KATO_CLAUDE_MODEL', type: 'text', label: 'Model override', picker: 'claude',
    placeholder: 'claude-sonnet-4-6', empty_label: "Not set — Claude Code's own default",
  };

  test('the Claude model field is a dropdown of every model, grouped, older versions included', async () => {
    fetchClaudeModels.mockResolvedValue(CLAUDE_MODELS);
    const onChange = vi.fn();
    const { container } = render(<SchemaField field={MODEL_FIELD} value="" onChange={onChange} />);
    const select = await waitFor(() => {
      const found = container.querySelector('select');
      expect(found.querySelectorAll('optgroup').length).toBe(2);
      return found;
    });
    expect([...select.querySelectorAll('optgroup')].map((group) => group.label))
      .toEqual(['Always the newest', 'Specific versions']);
    expect([...select.options].map((option) => option.textContent)).toEqual([
      "Not set — Claude Code's own default",
      'Sonnet 5.5 — always the newest Sonnet', 'Sonnet 5.5', 'Sonnet 4.6',
      'Other — type a model id…',
    ]);
    expect(container.querySelector('datalist')).toBeNull();
    fireEvent.change(select, { target: { value: 'claude-sonnet-4-6' } });
    expect(onChange).toHaveBeenLastCalledWith('claude-sonnet-4-6');
    expect(fetchOpenRouterModels).not.toHaveBeenCalled();
  });

  test('"Other" opens a box for any model id, and a saved one not on the list shows there', async () => {
    fetchClaudeModels.mockResolvedValue(CLAUDE_MODELS);
    const onChange = vi.fn();
    const { container, rerender } = render(<SchemaField field={MODEL_FIELD} value="" onChange={onChange} />);
    await waitFor(() => expect(container.querySelectorAll('optgroup').length).toBe(2));
    expect(screen.queryByRole('textbox')).toBeNull();
    fireEvent.change(container.querySelector('select'), { target: { value: '__other__' } });
    // Picking "Other" only opens the box: it never saves the menu's own marker.
    expect(onChange).not.toHaveBeenCalled();
    const box = screen.getByRole('textbox', { name: 'Model override: a model id' });
    fireEvent.change(box, { target: { value: 'claude-opus-4-1' } });
    expect(onChange).toHaveBeenLastCalledWith('claude-opus-4-1');
    // A saved value the list does not hold: "Other", with it in the box.
    rerender(<SchemaField field={MODEL_FIELD} value="sonnet 4.6" onChange={onChange} />);
    expect(container.querySelector('select').value).toBe('__other__');
    expect(screen.getByRole('textbox')).toHaveValue('sonnet 4.6');
  });

  test('before the list arrives a saved model is shown as it is, never as "Other"', () => {
    fetchClaudeModels.mockReturnValue(new Promise(() => {}));  // never answers
    const { container } = render(<SchemaField field={MODEL_FIELD} value="claude-sonnet-4-6" onChange={() => {}} />);
    expect(container.querySelector('select').value).toBe('claude-sonnet-4-6');
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  test('a field WITHOUT datalist renders no <datalist> and never fetches', () => {
    const { container } = render(
      <SchemaField
        field={{ key: 'OPENHANDS_LLM_API_KEY', type: 'text', label: 'Key' }}
        value=""
        onChange={() => {}}
      />,
    );
    expect(container.querySelector('datalist')).toBeNull();
    expect(container.querySelector('input').getAttribute('list')).toBeNull();
    expect(fetchOpenRouterModels).not.toHaveBeenCalled();
  });

  test('a failed catalogue fetch leaves the field usable (empty datalist)', async () => {
    fetchOpenRouterModels.mockResolvedValue([]);
    const { container } = render(
      <SchemaField field={OPENROUTER_FIELD} value="" onChange={() => {}} />,
    );
    await waitFor(() => expect(fetchOpenRouterModels).toHaveBeenCalled());
    expect(container.querySelectorAll('datalist option').length).toBe(0);
    // Still a normal text input — operator can type a slug by hand.
    expect(container.querySelector('input')).not.toBeNull();
  });
});

describe('SchemaField — select labels', () => {
  const MODE_FIELD = {
    key: 'KATO_CLAUDE_DEFAULT_MODE', type: 'select', label: 'Default mode for new tasks',
    options: ['', 'default', 'bypassPermissions'],
    option_labels: { '': 'Edit automatically', default: 'Manual', bypassPermissions: 'Auto' },
  };

  test('a select shows the schema\'s names for its options, its values unchanged', () => {
    const { container } = render(<SchemaField field={MODE_FIELD} value="" onChange={() => {}} />);
    const options = [...container.querySelectorAll('select option')];
    expect(options.map((o) => [o.value, o.textContent])).toEqual([
      ['', 'Edit automatically'], ['default', 'Manual'], ['bypassPermissions', 'Auto'],
    ]);
  });

  test('without names, a value is its own label and empty reads "(default)"', () => {
    expect(selectOptionLabel({ options: ['', 'low'] }, '')).toBe('(default)');
    expect(selectOptionLabel({ options: ['', 'low'] }, 'low')).toBe('low');
    expect(selectOptionLabel(null, 'x')).toBe('x');
  });
});

describe('SchemaField — textarea', () => {
  const TEXT_FIELD = {
    key: 'KATO_STANDING_INSTRUCTIONS', type: 'textarea', label: 'Standing instructions',
    max_length: 4000, placeholder: 'e.g. Answer in English.',
  };

  test('a textarea field is a multi-line box, capped, with a live count', () => {
    const onChange = vi.fn();
    const { container } = render(
      <SchemaField field={TEXT_FIELD} value={'one\ntwo'} onChange={onChange} />,
    );
    expect(container.querySelector('input')).toBeNull();
    const box = container.querySelector('textarea');
    expect(box.value).toBe('one\ntwo');
    expect(box.getAttribute('maxlength')).toBe('4000');
    expect(box.getAttribute('placeholder')).toBe('e.g. Answer in English.');
    expect(screen.getByText('7 / 4,000 characters')).toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'one\ntwo\nthree' } });
    expect(onChange).toHaveBeenCalledWith('one\ntwo\nthree');
  });

  test('without a cap there is no count', () => {
    const { container } = render(
      <SchemaField field={{ ...TEXT_FIELD, max_length: undefined }} value="x" onChange={() => {}} />,
    );
    expect(container.querySelector('textarea').hasAttribute('maxlength')).toBe(false);
    expect(screen.queryByText(/characters$/)).toBeNull();
  });
});
