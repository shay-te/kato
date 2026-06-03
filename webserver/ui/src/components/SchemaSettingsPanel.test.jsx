// Tests for SchemaField's datalist autocomplete — the OpenRouter model field
// pulls its suggestions live from the backend (same {models} shape as the chat
// model picker) instead of hardcoding slugs.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const fetchOpenRouterModels = vi.fn();

vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(() => Promise.resolve({ sections: [], values: {} })),
  updateAllSettings: vi.fn(() => Promise.resolve({})),
  fetchOpenRouterModels: (...args) => fetchOpenRouterModels(...args),
}));

import { SchemaField, resetDatalistCacheForTests } from './SchemaSettingsPanel.jsx';

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
