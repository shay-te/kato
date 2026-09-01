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

// The reveal must always make PROGRESS.
//
// ``computeEventLogWindow`` snaps the window start back to the turn boundary
// that opens the turn the cut lands in, so the rendered window is routinely
// larger than the size asked for. Adding a fixed increment to that size then
// lands inside the same turn and returns an identical window — and because
// the un-stick effect keyed on the visible COUNT, an unchanged window never
// cleared the in-flight flag. The log jammed on "Loading earlier events…"
// and every later scroll early-returned, with no button left to escape it.
describe('EventLog — a reveal inside one long turn still progresses', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 0; });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  // The fixture matters: the deadlock only appears when the turn STRADDLING
  // the window cut is longer than one chunk. ``computeEventLogWindow`` snaps
  // the start back to that turn's opening prompt, so the rendered window is
  // already far larger than the size asked for — and a fixed increment lands
  // inside the same turn and changes nothing. A transcript of plain
  // assistant messages never snaps and would pass against the broken code.
  function longTurnTranscript() {
    const entries = transcript(EVENT_LOG_WINDOW_SIZE + 400);
    // One prompt at the very start, one deep enough that the default window
    // cuts inside its (300-entry) turn.
    for (const at of [0, 200]) {
      entries[at] = {
        id: `p${at}`,
        source: 'stream',
        received_at_epoch: at + 1,
        raw: {
          type: 'user',
          message: { content: [{ type: 'text', text: `ask ${at}` }] },
        },
      };
    }
    return entries;
  }

  test('repeated scrolls keep revealing rather than sticking', async () => {
    const { container } = render(<EventLog entries={longTurnTranscript()} />);
    const node = logNode(container);
    // Bounded generously — the point is that each scroll STRICTLY reveals
    // more until nothing is left. If the in-flight flag ever sticks, the
    // very next iteration is a no-op and the count stops moving.
    for (let i = 0; i < 10 && screen.queryByRole('status'); i += 1) {
      const before = container.querySelectorAll('.bubble').length;
      await act(async () => { scrollTo(node, 0); });
      await waitFor(() => {
        expect(container.querySelectorAll('.bubble').length)
          .toBeGreaterThan(before);
      });
    }
    // Everything is shown, and the status line is gone rather than stuck on
    // "Loading earlier events…".
    expect(screen.queryByRole('status')).toBeNull();
  });
});
