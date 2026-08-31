// Reading upward through a long transcript.
//
// The old behaviour was a "Show N earlier events" button that flipped the
// window from a 200-entry tail to the ENTIRE history in one frame. Two
// problems, both reported: the operator only ever wanted a little more
// context above what they were reading, and nothing compensated the scroll
// position for the prepended height — so the log jumped to the top and they
// lost their place.
//
// Now: no button. Scrolling within 50px of the top reveals the next chunk,
// a progress bar shows while it renders, and the scroll position is anchored
// so the text under their eyes does not move.
//
// jsdom has no layout engine — scrollHeight is always 0 and scrollTop writes
// clamp — so the anchor ARITHMETIC is tested as a pure function over in
// utils/scrollUtils.test.js. What is asserted here is the wiring: what
// renders, and that a near-top scroll grows the window.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../hooks/useCommentStatusMap.js', () => ({
  useCommentStatusMap: () => ({}),
}));

import EventLog from './EventLog.jsx';
import { EVENT_LOG_WINDOW_SIZE } from './eventLogTruncation.js';

function transcript(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: `e${i}`,
    source: 'stream',
    received_at_epoch: i + 1,
    raw: {
      type: 'assistant',
      message: { content: [{ type: 'text', text: `message number ${i}` }] },
    },
  }));
}

function logNode(container) {
  return container.querySelector('#event-log');
}

// jsdom leaves every geometry at 0, which reads as "at the top" — fine for
// the reveal test, but we set it explicitly so each test states its intent.
function scrollTo(node, top) {
  Object.defineProperty(node, 'scrollTop', {
    value: top, writable: true, configurable: true,
  });
  fireEvent.scroll(node);
}

describe('EventLog — revealing older history', () => {
  beforeEach(() => {
    // The reveal defers one frame so the progress bar can paint first.
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 0; });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  test('the click-to-expand button is gone', () => {
    render(<EventLog entries={transcript(EVENT_LOG_WINDOW_SIZE + 50)} />);
    expect(screen.queryByRole('button', { name: /earlier event/i })).toBeNull();
  });

  test('it says how much history is still above', () => {
    render(<EventLog entries={transcript(EVENT_LOG_WINDOW_SIZE + 50)} />);
    expect(screen.getByRole('status').textContent).toMatch(/earlier event/i);
  });

  test('nothing is announced when the whole transcript fits', () => {
    render(<EventLog entries={transcript(5)} />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  test('scrolling to the top reveals more without a click', async () => {
    const { container } = render(
      <EventLog entries={transcript(EVENT_LOG_WINDOW_SIZE + 120)} />,
    );
    const before = container.querySelectorAll('.bubble').length;
    await act(async () => { scrollTo(logNode(container), 10); });
    await waitFor(() => {
      expect(container.querySelectorAll('.bubble').length).toBeGreaterThan(before);
    });
  });

  test('scrolling in the middle reveals nothing', async () => {
    const { container } = render(
      <EventLog entries={transcript(EVENT_LOG_WINDOW_SIZE + 120)} />,
    );
    const before = container.querySelectorAll('.bubble').length;
    await act(async () => { scrollTo(logNode(container), 4000); });
    expect(container.querySelectorAll('.bubble').length).toBe(before);
  });

  test('it stops once the whole transcript is shown', async () => {
    const { container } = render(
      <EventLog entries={transcript(EVENT_LOG_WINDOW_SIZE + 40)} />,
    );
    await act(async () => { scrollTo(logNode(container), 0); });
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    // A further scroll must not loop.
    await act(async () => { scrollTo(logNode(container), 0); });
    expect(screen.queryByRole('status')).toBeNull();
  });
});
