// Flash the browser tab title while something needs the operator.
//
// kato already fires a desktop notification, but notifications get missed —
// dismissed by accident, suppressed by focus assist, or gone by the time
// the operator looks back. An agent sits blocked on an approval for as
// long as it takes someone to notice, so a missed one costs wall-clock
// time. The tab title is the surface that keeps saying it.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';

import { useTitleAlert, TITLE_FLASH_MS } from './useTitleAlert.js';

const BASE = 'Kato — Planning UI';
const ALERT = 'Approval needed — kato';

function Harness({ active, message = ALERT }) {
  useTitleAlert(active, message);
  return null;
}

function setHidden(hidden) {
  Object.defineProperty(document, 'hidden', {
    configurable: true, get: () => hidden,
  });
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  document.title = BASE;
  Object.defineProperty(document, 'hidden', {
    configurable: true, get: () => true,
  });
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  document.title = BASE;
});

describe('useTitleAlert', () => {
  test('nothing pending leaves the title alone', () => {
    render(<Harness active={false} />);
    expect(document.title).toBe(BASE);
  });

  test('a pending ask shows the alert immediately', () => {
    // Waiting a full interval before the first flash wastes the moment the
    // operator is most likely glancing at their tabs.
    render(<Harness active />);
    expect(document.title).toBe(ALERT);
  });

  test('it alternates so the tab reads as motion, not a rename', () => {
    render(<Harness active />);
    expect(document.title).toBe(ALERT);
    act(() => { vi.advanceTimersByTime(TITLE_FLASH_MS); });
    expect(document.title).toBe(BASE);
    act(() => { vi.advanceTimersByTime(TITLE_FLASH_MS); });
    expect(document.title).toBe(ALERT);
  });

  test('resolving the ask restores the original title', () => {
    const { rerender } = render(<Harness active />);
    expect(document.title).toBe(ALERT);
    rerender(<Harness active={false} />);
    expect(document.title).toBe(BASE);
  });

  test('it does NOT flash while the tab is in front', () => {
    // The approval dialog is already on screen; animating the title too is
    // noise, and noise is what teaches people to stop reading it.
    setHidden(false);
    render(<Harness active />);
    expect(document.title).toBe(BASE);
  });

  test('coming back to the tab restores the title at once', () => {
    // Leaving "Approval needed" in a tab the operator is looking at reads
    // as a second, phantom request.
    render(<Harness active />);
    expect(document.title).toBe(ALERT);
    setHidden(false);
    expect(document.title).toBe(BASE);
  });

  test('leaving the tab again resumes the flash', () => {
    render(<Harness active />);
    setHidden(false);
    expect(document.title).toBe(BASE);
    setHidden(true);
    expect(document.title).toBe(ALERT);
  });

  test('unmounting restores the title', () => {
    const { unmount } = render(<Harness active />);
    expect(document.title).toBe(ALERT);
    unmount();
    expect(document.title).toBe(BASE);
  });

  test('a title changed while idle is the one restored', () => {
    // The base is captured when the flash STARTS, not at mount — restoring
    // a stale title would be worse than not restoring at all.
    const { rerender } = render(<Harness active={false} />);
    document.title = 'Kato — UNA-2990';
    rerender(<Harness active />);
    expect(document.title).toBe(ALERT);
    rerender(<Harness active={false} />);
    expect(document.title).toBe('Kato — UNA-2990');
  });

  test('the message can carry a count', () => {
    render(<Harness active message="(3) Approval needed — kato" />);
    expect(document.title).toBe('(3) Approval needed — kato');
  });
});
