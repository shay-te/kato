// A round that ended the loop says why. A failed round can have no review, no
// findings and no tests, so without this it opens onto nothing at all.

import { describe, test, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import ReviewLoopRoundList from './ReviewLoopRoundList.jsx';

const REASON = 'there are no changes to review';
const failedRound = {
  number: 1, findings: [], counts: {}, outcome: 'failed', reviewed_at: 0, test_runs: [],
};

function renderList(props = {}) {
  const loop = { loop_id: `loop-${Math.random()}`, rounds: [failedRound], ...props.loop };
  return render(
    <ReviewLoopRoundList taskId="T-1" loop={loop} endReason={REASON} {...props.list} />,
  );
}

describe('ReviewLoopRoundList', () => {
  test('a failed round says why while collapsed', () => {
    renderList();
    expect(screen.getByText(REASON)).toBeInTheDocument();
    expect(screen.queryByText('not reviewed yet')).not.toBeInTheDocument();
  });

  test('and still says why when opened', () => {
    const { container } = renderList();
    fireEvent.click(container.querySelector('.review-loop-round button'));
    expect(container.querySelector('.review-loop-round-reason')).toHaveTextContent(REASON);
  });

  test('only the round the loop ended in carries the reason', () => {
    const earlier = { ...failedRound, number: 1, reviewed_at: 5, outcome: 'fixed' };
    const last = { ...failedRound, number: 2 };
    const { container } = renderList({ loop: { rounds: [earlier, last] } });
    const rounds = container.querySelectorAll('.review-loop-round');
    expect(rounds[0]).not.toHaveTextContent(REASON);
    expect(rounds[1]).toHaveTextContent(REASON);
  });

  test('a round that was reviewed keeps its counts in the row', () => {
    const reviewed = { ...failedRound, reviewed_at: 5, counts: { MINOR: 2 } };
    const { container } = renderList({ loop: { rounds: [reviewed] } });
    expect(container.querySelector('.review-loop-round')).toHaveTextContent('2 minor');
  });

  // UNA-3406: tests that cannot start in the task folder are listed apart.
  test('a test run lists what could not run here, apart from failures', () => {
    const tested = { ...failedRound, outcome: 'clean', reviewed_at: 5, test_runs: [{
      passed: true, command: 'pytest', summary: '38 passed', failures: [],
      cannot_run: ["tests.test_pdf_service: No module named 'cloudinary'"],
    }] };
    const { container } = renderList({ loop: { rounds: [tested] }, list: { endReason: '' } });
    fireEvent.click(container.querySelector('.review-loop-round button'));
    const run = container.querySelector('.review-loop-tests');
    expect(run).toHaveClass('is-warn');
    expect(run).toHaveTextContent('Tests passed — 38 passed (pytest) · 1 could not run here');
    expect(run).toHaveTextContent("Could not run here — not this task's code to fix:");
    expect(run).toHaveTextContent("tests.test_pdf_service: No module named 'cloudinary'");
  });

  // UNA-3454: tests that already failed before the task are listed apart too.
  test('a test run lists what already failed before the task, apart from failures', () => {
    const tested = { ...failedRound, outcome: 'clean', reviewed_at: 5, test_runs: [{
      passed: true, command: 'jest', summary: '274 passed', failures: [],
      already_failing: ['src/__tests__/tabs.test.js › one row: expects 11, gets 12 (same stashed)'],
    }] };
    const { container } = renderList({ loop: { rounds: [tested] }, list: { endReason: '' } });
    fireEvent.click(container.querySelector('.review-loop-round button'));
    const run = container.querySelector('.review-loop-tests');
    expect(run).toHaveClass('is-warn');
    expect(run).toHaveTextContent('Tests passed — 274 passed (jest) · 1 already failing before this task');
    expect(run).toHaveTextContent('Already failing before this task — not sent to be fixed:');
    expect(run).toHaveTextContent('expects 11, gets 12 (same stashed)');
    expect(run).not.toHaveTextContent('Could not run here');
  });

  // UNA-3454: a round that ended before its review shows the loop's whole end
  // reason as its summary — one line, all of it on hover, never over the clock.
  test('an unreviewed round\'s long end reason is one line with all of it on hover', () => {
    const reason = 'the tests still fail after 3 fixes in round 1 (src/a.test.js › x; src/b.test.js › y)';
    const stuck = { ...failedRound, outcome: 'stuck', reviewed_at: 0, findings: [] };
    const { container } = renderList({ loop: { rounds: [stuck] }, list: { endReason: reason } });
    const summary = container.querySelector('.review-loop-round-counts');
    expect(summary).toHaveTextContent(reason);
    expect(summary).toHaveAttribute('title', reason);
  });

  test('a running round reads as before', () => {
    renderList({ list: { endReason: '' } });
    expect(screen.getByText('not reviewed yet')).toBeInTheDocument();
  });
});
