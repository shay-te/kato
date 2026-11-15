// "Sign in later": kato's main screen offers Claude Code's sign-in while it is
// signed out. The API is the one stand-in, answering the way
// quick_start_routes.py does.
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchQuickStartChecks: vi.fn(),
  startClaudeSignIn: vi.fn(),
  fetchClaudeSignIn: vi.fn(),
  sendClaudeSignInCode: vi.fn(),
  cancelClaudeSignIn: vi.fn(),
}));

import * as api from '../api.js';
import ClaudeSignInBanner from './ClaudeSignInBanner.jsx';

const claude = (agent) => ({ agent: { agent: 'claude', installed: true, version: '2.1.325', ...agent }, git: { installed: true } });
const SIGNED_OUT = claude({ logged_in: false });
const SIGNED_IN = claude({ logged_in: true });
const NOTE = /Claude Code is not signed in/;

beforeEach(() => { vi.clearAllMocks(); });

describe('ClaudeSignInBanner', () => {
  test('signed out: says so and offers the sign-in', async () => {
    api.fetchQuickStartChecks.mockResolvedValue(SIGNED_OUT);
    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: { state: 'waiting', link: 'https://claude.example/x', asks_for_code: false } });
    render(<ClaudeSignInBanner needed />);
    expect(await screen.findByText(NOTE)).toBeInTheDocument();
    expect(api.fetchQuickStartChecks).toHaveBeenCalledWith('claude');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Claude' }));
    expect(await screen.findByText(/A browser window opened to sign in/)).toBeInTheDocument();
  });

  test('nothing to say: signed in, not installed, cannot tell, or the question failed', async () => {
    for (const answer of [SIGNED_IN, claude({ installed: false, logged_in: null }), claude({ logged_in: null })]) {
      api.fetchQuickStartChecks.mockResolvedValueOnce(answer);
      const { container, unmount } = render(<ClaudeSignInBanner needed />);
      await waitFor(() => expect(api.fetchQuickStartChecks).toHaveBeenCalled());
      expect(container).toBeEmptyDOMElement();
      unmount();
      vi.clearAllMocks();
    }
    api.fetchQuickStartChecks.mockRejectedValueOnce(new Error('offline'));
    const { container } = render(<ClaudeSignInBanner needed />);
    await waitFor(() => expect(api.fetchQuickStartChecks).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  test('not asked at all while Claude Code is not the agent that matters', () => {
    const { container } = render(<ClaudeSignInBanner needed={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(api.fetchQuickStartChecks).not.toHaveBeenCalled();
  });

  test('a sign-in done elsewhere clears it when the window comes back', async () => {
    api.fetchQuickStartChecks.mockResolvedValueOnce(SIGNED_OUT);
    render(<ClaudeSignInBanner needed />);
    await screen.findByText(NOTE);
    api.fetchQuickStartChecks.mockResolvedValueOnce(SIGNED_IN);
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(screen.queryByText(NOTE)).toBeNull());
    expect(api.fetchQuickStartChecks).toHaveBeenCalledTimes(2);
    // Signed in: the window coming back asks nothing more.
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(api.fetchQuickStartChecks).toHaveBeenCalledTimes(2);
  });
});
