// Tests for OrchestratorActivityFeed. Shows scan-tick / status-change
// rows in the right pane when no task is selected. Empty state when
// history is empty or missing.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import OrchestratorActivityFeed from './OrchestratorActivityFeed.jsx';


function _entry(seq, level, message, epoch = 1700000000) {
  return { sequence: seq, level, message, epoch };
}


describe('OrchestratorActivityFeed', () => {

  test('renders the empty-state copy when history is empty', () => {
    render(<OrchestratorActivityFeed history={[]} />);
    expect(screen.getByText(/No activity yet/i)).toBeInTheDocument();
    expect(screen.getByText('orchestrator activity')).toBeInTheDocument();
  });

  test('renders the empty-state when history is undefined', () => {
    render(<OrchestratorActivityFeed />);
    expect(screen.getByText(/No activity yet/i)).toBeInTheDocument();
  });

  test('renders each history entry message', () => {
    const { container } = render(<OrchestratorActivityFeed history={[
      _entry(1, 'INFO', 'scan tick 1'),
      _entry(2, 'INFO', 'scan tick 2'),
      _entry(3, 'WARN', 'rate limit close'),
    ]} />);
    // Messages are token-colorized (numbers/paths/URLs wrapped in
    // <span>s), so getByText on the whole string won't match — the
    // text is split across nodes. Assert on each row's combined
    // textContent instead, which still proves the message rendered.
    const rows = [...container.querySelectorAll('.orchestrator-feed-row .msg')]
      .map((el) => el.textContent);
    expect(rows).toContain('scan tick 1');
    expect(rows).toContain('scan tick 2');
    expect(rows).toContain('rate limit close');
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument();
  });

  test('renders the footer pointing at left-pane tabs when entries exist', () => {
    render(<OrchestratorActivityFeed history={[_entry(1, 'INFO', 'msg')]} />);
    expect(screen.getByText(/Pick a task on the left/i)).toBeInTheDocument();
  });

  test('ERROR-level row gets the "error" modifier class', () => {
    const { container } = render(<OrchestratorActivityFeed history={[
      _entry(1, 'ERROR', 'boom'),
    ]} />);
    expect(container.querySelector('.orchestrator-feed-row.error')).toBeInTheDocument();
  });

  test('WARN/WARNING-level row gets the "warn" modifier class', () => {
    const { container } = render(<OrchestratorActivityFeed history={[
      _entry(1, 'WARNING', 'careful'),
    ]} />);
    expect(container.querySelector('.orchestrator-feed-row.warn')).toBeInTheDocument();
  });

  test('renders a truncated 4-char level tag (e.g. "INFO", "WARN")', () => {
    render(<OrchestratorActivityFeed history={[
      _entry(1, 'WARNING', 'msg-warn'),
    ]} />);
    // (entry.level || '').slice(0, 4) → "WARN"
    expect(screen.getByText('WARN')).toBeInTheDocument();
  });
});
