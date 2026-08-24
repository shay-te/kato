import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AgentBackendChip, { backendLabel } from './AgentBackendChip.jsx';

describe('backendLabel', () => {
  it('names the backends an operator recognises', () => {
    expect(backendLabel('claude')).toBe('Claude');
    expect(backendLabel('codex')).toBe('Codex');
    expect(backendLabel('openhands')).toBe('OpenHands');
  });

  it('tolerates the casing and padding a record may carry', () => {
    expect(backendLabel('  CODEX ')).toBe('Codex');
  });

  it('shows an unknown backend verbatim rather than hiding it', () => {
    // A backend kato does not know yet is still worth telling the operator
    // about — silently dropping it would look like a chat with no backend.
    expect(backendLabel('someagent')).toBe('someagent');
  });

  it('has no label for a missing backend', () => {
    for (const value of ['', '   ', null, undefined]) {
      expect(backendLabel(value)).toBe('');
    }
  });
});

describe('AgentBackendChip', () => {
  it('renders the backend name', () => {
    render(<AgentBackendChip backend="codex" />);
    expect(screen.getByText('Codex')).toBeTruthy();
  });

  it('renders NOTHING for a chat that predates the field', () => {
    // These records exist on every operator's disk. A guessed chip would
    // label an old Claude chat with whatever backend is configured today.
    const { container } = render(<AgentBackendChip backend="" />);
    expect(container.innerHTML).toBe('');
  });

  it('carries a per-backend class so the chip can be styled or found', () => {
    const { container } = render(<AgentBackendChip backend="Claude" />);
    expect(container.querySelector('.agent-backend-chip-claude')).toBeTruthy();
  });

  it('explains itself on hover', () => {
    render(<AgentBackendChip backend="codex" />);
    expect(screen.getByTitle('This chat runs on Codex')).toBeTruthy();
  });
});
