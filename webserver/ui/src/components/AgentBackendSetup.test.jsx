/**
 * An unconfigured agent tab explains itself instead of opening a dead chat.
 *
 * Both agent tabs always exist — hiding the Codex tab is how an operator
 * never discovers kato can run it. So selecting a backend whose CLI is
 * missing has to show what to install, using the transport's own validator
 * text rather than a second copy that drifts from it.
 */
import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import AgentBackendSetup from './AgentBackendSetup.jsx';

const CODEX_ERROR = [
  'Codex CLI ("codex") was not found on PATH.',
  '',
  'Install Codex CLI (works on macOS, Linux, and Windows):',
  '',
  '    npm install -g @openai/codex',
].join('\n');

afterEach(() => { cleanup(); });

describe('AgentBackendSetup', () => {
  test('names the backend that needs setting up', () => {
    render(<AgentBackendSetup backend="codex" error={CODEX_ERROR} />);
    expect(screen.getByText(/Codex isn't set up on this host/)).toBeTruthy();
  });

  test('shows the validator text verbatim, including the install command', () => {
    const { container } = render(
      <AgentBackendSetup backend="codex" error={CODEX_ERROR} />,
    );
    const detail = container.querySelector('.agent-backend-setup-detail');
    expect(detail.textContent).toContain('npm install -g @openai/codex');
    // A <pre>: the message lays its commands out on indented lines and
    // collapsing that whitespace would make them unreadable.
    expect(detail.tagName).toBe('PRE');
  });

  test('falls back to a pointer at the binary setting with no detail', () => {
    const { container } = render(<AgentBackendSetup backend="codex" error="" />);
    const detail = container.querySelector('.agent-backend-setup-detail');
    expect(detail.textContent).toContain('Codex agent');
    expect(detail.tagName).toBe('P');
  });

  test('"Check again" re-runs the probe', () => {
    const onRecheck = vi.fn();
    render(
      <AgentBackendSetup backend="codex" error={CODEX_ERROR} onRecheck={onRecheck} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  test('the recheck button reports and disables while in flight', () => {
    render(<AgentBackendSetup backend="codex" error={CODEX_ERROR} rechecking />);
    const button = screen.getByRole('button', { name: 'Checking…' });
    expect(button.disabled).toBe(true);
  });

  test('reassures that the other tabs still work', () => {
    render(<AgentBackendSetup backend="codex" error={CODEX_ERROR} />);
    expect(screen.getByText(/other agent tabs are unaffected/)).toBeTruthy();
  });
});

describe('AgentBackendSetup — installed but not wired', () => {
  test('says to restart kato rather than blaming the binary path', () => {
    // ready + no error + not wired = the CLI answered fine, kato just has
    // no manager for it yet. Telling this operator to check the binary path
    // sends them hunting for a fault that does not exist.
    render(<AgentBackendSetup backend="codex" error="" wired={false} />);
    expect(screen.getByText(/Restart kato to pick it up/)).toBeTruthy();
  });

  test('a real install error still wins over the restart hint', () => {
    const { container } = render(
      <AgentBackendSetup backend="codex" error={CODEX_ERROR} wired={false} />,
    );
    expect(container.querySelector('pre').textContent)
      .toContain('npm install -g @openai/codex');
    expect(screen.queryByText(/Restart kato/)).toBeNull();
  });
});
