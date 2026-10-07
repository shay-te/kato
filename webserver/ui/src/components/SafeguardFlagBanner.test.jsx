// The safeguard-flag banner: shows above the composer on a flagged turn and
// offers a retry on the fallback model. Real component + api helpers; the ONE
// stand-in is the network (``fetch`` answers the fallback + the retry POST).
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import SafeguardFlagBanner from './SafeguardFlagBanner.jsx';

const FLAG = [
  "API Error: Opus 5.5's safeguards flagged this session",
  '(https://www.anthropic.com/legal/aup).',
  'Details: [cyber]',
  'Request ID: req_011ABC',
].join('\n');

function flaggedEntries() {
  return [
    { source: 'server', raw: { type: 'user', message: { content: 'scan the host' } } },
    { source: 'server', raw: { type: 'result', is_error: true, result: FLAG } },
  ];
}

let posted;
beforeEach(() => {
  posted = [];
  vi.stubGlobal('fetch', async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    if (url.includes('/api/safeguard-fallback')) {
      return { ok: true, status: 200, json: async () => ({ model: 'claude-opus-4-8', label: 'Opus 4.8' }) };
    }
    if (url.includes('/retry-on-fallback')) {
      posted.push(`${method} ${url}`);
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('SafeguardFlagBanner', () => {
  test('shows the flag + a retry on the fallback, and retries on click', async () => {
    render(<SafeguardFlagBanner taskId="UNA-1" entries={flaggedEntries()} />);
    await screen.findByRole('alert');
    expect(screen.getByText(/safeguards flagged this turn \(cyber\)/)).toBeInTheDocument();
    expect(screen.getByText('Request req_011ABC')).toBeInTheDocument();

    const button = await screen.findByRole('button', { name: /Retry on Opus 4\.8/ });
    fireEvent.click(button);
    await waitFor(() => expect(posted).toContain('POST /api/sessions/UNA-1/retry-on-fallback'));
  });

  test('renders nothing when the latest turn is not flagged', async () => {
    const ok = [{ source: 'server', raw: { type: 'result', is_error: false, result: 'done' } }];
    const { container } = render(<SafeguardFlagBanner taskId="UNA-1" entries={ok} />);
    // Give the fallback fetch a tick; still nothing, because there is no flag.
    await Promise.resolve();
    expect(container.querySelector('.safeguard-flag-banner')).toBeNull();
  });

  test('renders nothing when no fallback model is configured', async () => {
    vi.stubGlobal('fetch', async (url) => (
      url.includes('/api/safeguard-fallback')
        ? { ok: true, status: 200, json: async () => ({ model: '', label: '' }) }
        : { ok: false, status: 404, json: async () => ({}) }
    ));
    const { container } = render(<SafeguardFlagBanner taskId="UNA-1" entries={flaggedEntries()} />);
    await Promise.resolve();
    await Promise.resolve();
    expect(container.querySelector('.safeguard-flag-banner')).toBeNull();
  });
});
