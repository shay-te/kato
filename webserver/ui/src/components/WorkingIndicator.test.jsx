// Component tests for ``WorkingIndicator`` — the "Claude is
// thinking…" overlay. The operator-trust contract:
//
//   - Hides entirely when ``active=false`` (no false-positive
//     "working" while idle).
//   - Shows a STALLED warning when active=true but no events have
//     arrived for ≥45 seconds (so the operator can act).
//   - Shows the WAITING-FOR-APPROVAL variant when the boolean is set
//     (different glyph, different aria text).
//
// These were exactly the symptoms of the "Claude is stuck not
// responding" bug — pinning the visibility logic in isolation lets
// CI catch regressions.

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';

import WorkingIndicator from './WorkingIndicator.jsx';


describe('WorkingIndicator — visibility', () => {

  test('renders NOTHING when active=false', () => {
    const { container } = render(<WorkingIndicator active={false} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when active is undefined (no false-positive)', () => {
    const { container } = render(<WorkingIndicator />);
    expect(container.firstChild).toBeNull();
  });

  test('renders the "thinking" variant when active=true and no other state', () => {
    render(<WorkingIndicator active={true} />);
    const status = screen.getByRole('status');
    expect(status).toBeInTheDocument();
    // One of the phrases should be visible; the exact word rotates
    // every 2.2s but at least the indicator is present.
    expect(status.textContent).toMatch(/\S/);  // non-empty text
  });

  test('renders the WAITING-FOR-APPROVAL variant when flag is set', () => {
    render(<WorkingIndicator active={true} waitingForApproval={true} />);
    expect(screen.getByText(/waiting for approval/i)).toBeInTheDocument();
  });

  test('WAITING-FOR-APPROVAL variant carries the is-waiting-approval class', () => {
    const { container } = render(
      <WorkingIndicator active={true} waitingForApproval={true} />,
    );
    expect(container.firstChild).toHaveClass('is-waiting-approval');
  });
});


describe('WorkingIndicator — stalled detection', () => {
  let originalNow;

  beforeEach(() => {
    vi.useFakeTimers();
    originalNow = Date.now;
  });
  afterEach(() => {
    Date.now = originalNow;
    vi.useRealTimers();
  });

  test('does NOT show stalled when activity is recent (under threshold)', () => {
    const now = Date.now();
    vi.setSystemTime(now);
    // Last event 10 seconds ago — well under the 180s threshold.
    render(
      <WorkingIndicator active={true} lastEventAt={now - 10_000} />,
    );
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();
  });

  test('a long but normal quiet gap (2.5 min) is NOT flagged stalled', () => {
    // Regression for the reported false positive: a real long tool
    // run (tests / install / deep Read) goes minutes between
    // streamed events and must NOT read as "stalled". 150s is well
    // past the old 45s threshold but under the new 180s one.
    const now = Date.now();
    vi.setSystemTime(now);
    render(<WorkingIndicator active={true} lastEventAt={now - 150_000} />);
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();
  });

  test('flips to STALLED warning after 3 minutes of silence', () => {
    const start = 1_000_000;
    vi.setSystemTime(start);

    render(
      <WorkingIndicator active={true} lastEventAt={start} />,
    );

    // Initially: not stalled.
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();

    // Advance system clock past the 180s threshold without new
    // events. The component's internal interval re-renders to
    // detect it.
    act(() => {
      vi.setSystemTime(start + 181_000);
      vi.advanceTimersByTime(181_000);
    });

    expect(screen.getByText(/may be stalled/i)).toBeInTheDocument();
  });

  test('STALLED variant shows the elapsed time', () => {
    const start = 1_000_000;
    vi.setSystemTime(start);
    const { container } = render(
      <WorkingIndicator active={true} lastEventAt={start} />,
    );
    act(() => {
      vi.setSystemTime(start + 200_000);  // 3m20s
      vi.advanceTimersByTime(200_000);
    });
    // The whole indicator's text should mention some "Xm" or "Xs"
    // duration. We accept either compact format since the exact
    // accumulated time depends on tick scheduling.
    const text = container.textContent || '';
    expect(text).toMatch(/may be stalled/i);
    expect(text).toMatch(/\d+m(\d{2}s)?|\d+s/);
  });

  test('STALLED warning has is-stalled CSS class for distinct styling', () => {
    const start = 1_000_000;
    vi.setSystemTime(start);
    const { container } = render(
      <WorkingIndicator active={true} lastEventAt={start} />,
    );
    act(() => {
      vi.setSystemTime(start + 200_000);
      vi.advanceTimersByTime(200_000);
    });
    expect(container.firstChild).toHaveClass('is-stalled');
  });

  test('stalled state offers a one-click "continue" nudge', () => {
    const start = 1_000_000;
    vi.setSystemTime(start);
    const onContinue = vi.fn();
    render(
      <WorkingIndicator
        active={true} lastEventAt={start} onContinue={onContinue}
      />,
    );
    // Not stalled yet → no nudge button.
    expect(screen.queryByText(/send .continue./i)).not.toBeInTheDocument();
    act(() => {
      vi.setSystemTime(start + 200_000);
      vi.advanceTimersByTime(200_000);
    });
    const btn = screen.getByRole('button', { name: /send .continue./i });
    fireEvent.click(btn);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  test('no nudge button when onContinue is not provided', () => {
    const start = 1_000_000;
    vi.setSystemTime(start);
    render(<WorkingIndicator active={true} lastEventAt={start} />);
    act(() => {
      vi.setSystemTime(start + 200_000);
      vi.advanceTimersByTime(200_000);
    });
    expect(screen.getByText(/may be stalled/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /continue/i }))
      .not.toBeInTheDocument();
  });

  test('lastEventAt=0 disables stalled detection entirely', () => {
    // No event-time signal → can't compute idle duration → never
    // flip to stalled. Avoids a stuck "stalled" right after spawn.
    const start = 1_000_000;
    vi.setSystemTime(start);
    render(<WorkingIndicator active={true} lastEventAt={0} />);
    act(() => {
      vi.setSystemTime(start + 600_000);  // 10 minutes
      vi.advanceTimersByTime(600_000);
    });
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();
  });
});


describe('WorkingIndicator — accessibility', () => {

  test('uses role=status and aria-live=polite so screen readers announce changes', () => {
    render(<WorkingIndicator active={true} />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
  });
});
