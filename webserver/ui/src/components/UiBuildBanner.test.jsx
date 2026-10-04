import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';

import UiBuildBanner from './UiBuildBanner.jsx';
import { uiBuildStore, STABLE_MS } from '../stores/uiBuildStore.js';

beforeEach(() => {
  uiBuildStore.resetForTest('100');
  window.sessionStorage.clear();
  // In use: the one case the reload is not automatic, so the banner shows.
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
  document.hasFocus = () => true;
});

describe('UiBuildBanner', () => {
  test('renders nothing while the window is on the current build', () => {
    const { container } = render(<UiBuildBanner />);
    expect(container.firstChild).toBeNull();
  });

  test('offers the reload when a newer bundle is on disk', () => {
    const reload = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true, value: { ...window.location, reload },
    });
    render(<UiBuildBanner />);
    act(() => {
      uiBuildStore.observe('200', 0);
      uiBuildStore.observe('200', STABLE_MS);
    });

    expect(screen.getByRole('status').textContent).toContain(
      'A newer version of this page is ready',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reload now' }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
