// The context meter is the operator's only warning that the window is
// filling. Kato keeps ONE session per task and never compacts or restarts it
// on its own, so acting in time is entirely on them — which makes a wrong
// number worse than no number.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import ContextMeter from './ContextMeter.jsx';

const meter = () => screen.getByRole('status');

describe('ContextMeter', () => {
  test('reports REMAINING context, not used', () => {
    render(<ContextMeter usage={{ used_tokens: 50_000, limit_tokens: 200_000 }} />);
    expect(screen.getByText('75% left')).toBeInTheDocument();
  });

  test('the bar fills as context is consumed', () => {
    const { container } = render(
      <ContextMeter usage={{ used_tokens: 150_000, limit_tokens: 200_000 }} />,
    );
    expect(container.querySelector('.context-meter-fill')).toHaveStyle({ width: '75%' });
  });

  test('renders NOTHING when there is no reading', () => {
    // A placeholder in a row of pills is clutter, and an empty gauge is one
    // glance from being read as a measurement. Absent says "no data" clearly.
    for (const usage of [null, undefined, {}, { used_tokens: 0, limit_tokens: 0 },
                         { used_tokens: 5, limit_tokens: 0 },
                         { used_tokens: 0, limit_tokens: 200_000 }]) {
      const { container, unmount } = render(<ContextMeter usage={usage} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  test('warns while there is still room to run /compact', () => {
    // Compacting costs a turn, so "act now" has to fire before the wall.
    const { unmount } = render(
      <ContextMeter usage={{ used_tokens: 160_000, limit_tokens: 200_000 }} />,
    );
    expect(meter()).toHaveClass('context-meter--low');
    unmount();
    render(<ContextMeter usage={{ used_tokens: 195_000, limit_tokens: 200_000 }} />);
    expect(meter()).toHaveClass('context-meter--critical');
  });

  test('a healthy window is not styled as a warning', () => {
    render(<ContextMeter usage={{ used_tokens: 10_000, limit_tokens: 200_000 }} />);
    expect(meter()).toHaveClass('context-meter--ok');
  });

  test('the 1M window is sized from the reported limit, not assumed', () => {
    render(<ContextMeter usage={{ used_tokens: 200_000, limit_tokens: 1_000_000 }} />);
    // The same 200k that would exhaust a standard window barely dents this one.
    expect(screen.getByText('80% left')).toBeInTheDocument();
  });

  test('token counts are humanised in the accessible label', () => {
    render(<ContextMeter usage={{ used_tokens: 24_121, limit_tokens: 1_000_000 }} />);
    expect(meter()).toHaveAccessibleName(/24k of 1\.0M used/);
  });

  test('over-full never renders a negative percentage', () => {
    render(<ContextMeter usage={{ used_tokens: 260_000, limit_tokens: 200_000 }} />);
    expect(screen.getByText('0% left')).toBeInTheDocument();
  });

  test('it reports the window only — cost is a separate indicator', () => {
    // Two different questions. A chat at 51% left can still be re-reading
    // half a million tokens a turn; that lives in ChatCostDot.
    render(<ContextMeter usage={{
      used_tokens: 490_000, limit_tokens: 1_000_000, baseline_tokens: 40_000,
    }} />);
    expect(screen.getByText(/51% left/)).toBeInTheDocument();
    expect(screen.queryByText(/fresh|expensive/)).toBeNull();
  });

  test('the tooltip opens upward and right-aligned', () => {
    const { container } = render(<ContextMeter usage={{
      used_tokens: 100_000, limit_tokens: 1_000_000,
    }} />);
    const meter = container.querySelector('.context-meter');
    expect(meter.className).toContain('tooltip-above');
    expect(meter.className).toContain('tooltip-end');
  });

  test('junk values render nothing rather than NaN', () => {
    const { container } = render(
      <ContextMeter usage={{ used_tokens: 'lots', limit_tokens: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
