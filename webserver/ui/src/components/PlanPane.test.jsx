// Tests for PlanPane — the centre-pane view that renders the agent's
// captured plan.md as markdown for review.

import { describe, test, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import PlanPane from './PlanPane.jsx';

describe('PlanPane', () => {
  test('renders the plan markdown', () => {
    const { container } = render(
      <PlanPane content={'# My Plan\n\n1. First step\n2. Second step'} />,
    );
    expect(container.querySelector('h1')).toHaveTextContent('My Plan');
    expect(container.querySelector('ol')).toBeTruthy();
    expect(screen.getByText('First step')).toBeTruthy();
  });

  test('shows the header with title and pill', () => {
    const { container } = render(<PlanPane content={'see below'} />);
    expect(container.querySelector('.plan-pane-title')).toHaveTextContent('Plan');
    expect(container.querySelector('.plan-pane-pill')).toHaveTextContent('review');
  });

  test('shows an empty state when there is no plan', () => {
    render(<PlanPane content={''} />);
    expect(screen.getByText('No plan yet.')).toBeTruthy();
  });

  test('treats whitespace-only content as empty', () => {
    render(<PlanPane content={'   \n  '} />);
    expect(screen.getByText('No plan yet.')).toBeTruthy();
  });
});


test('the plan can be dismissed like every other centre-pane body', () => {
  // It was the one view that took the centre column and would not give it
  // back: a file tab has its ✕, the orchestrator feed has one, and the plan
  // opens ITSELF when the agent writes a new one.
  const onClose = vi.fn();
  render(<PlanPane content="# hi" onClose={onClose} />);
  fireEvent.click(screen.getByRole('button', { name: /close plan/i }));
  expect(onClose).toHaveBeenCalled();
});

test('no close button when the host offers no handler', () => {
  render(<PlanPane content="# hi" />);
  expect(screen.queryByRole('button', { name: /close plan/i })).toBeNull();
});
