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

  test('a running round reads as before', () => {
    renderList({ list: { endReason: '' } });
    expect(screen.getByText('not reviewed yet')).toBeInTheDocument();
  });
});
