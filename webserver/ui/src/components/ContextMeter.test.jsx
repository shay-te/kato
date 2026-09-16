// The context meter is the operator's only warning that the window is
// filling. Kato keeps ONE session per task and never compacts or restarts it
// on its own, so acting in time is entirely on them — which makes a wrong
// number worse than no number.

import { describe, test, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

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


// "when this is very close to finishing or maybe when I click on this I want
// him to show me an option to launch a new chat with the summary of the
// previous chat".
describe('ContextMeter — new chat from a summary', () => {
  const LOW = { used_tokens: 813_000, limit_tokens: 1_000_000 };
  const HEALTHY = { used_tokens: 100_000, limit_tokens: 1_000_000 };
  const trigger = () => screen.getByRole('button', { name: /start a new chat from a summary/i });
  const offer = () => screen.getByRole('menuitem', { name: /new chat from a summary|writing the summary/i });

  test('clicking the meter offers the new chat', () => {
    render(<ContextMeter usage={HEALTHY} onStartChatFromSummary={vi.fn()} />);
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(offer()).toHaveTextContent(/stays in the chats menu/i);
  });

  test('a low window says the offer is there before it is clicked', () => {
    const { unmount } = render(
      <ContextMeter usage={HEALTHY} onStartChatFromSummary={vi.fn()} />,
    );
    expect(trigger()).not.toHaveTextContent(/new chat/i);
    unmount();
    render(<ContextMeter usage={LOW} onStartChatFromSummary={vi.fn()} />);
    expect(trigger()).toHaveTextContent(/19% left\s*· new chat/);
    expect(trigger().getAttribute('data-tooltip')).toMatch(/new chat from a summary/i);
  });

  test('picking the offer starts it and closes the menu', () => {
    const onStart = vi.fn();
    render(<ContextMeter usage={LOW} onStartChatFromSummary={onStart} />);
    fireEvent.click(trigger());
    fireEvent.click(offer());
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  test('the offer waits while a turn is running, and says so', () => {
    const onStart = vi.fn();
    render(<ContextMeter usage={LOW} onStartChatFromSummary={onStart} turnInFlight />);
    fireEvent.click(trigger());
    expect(offer()).toBeDisabled();
    expect(screen.getByRole('menu')).toHaveTextContent(/once the current turn finishes/i);
  });

  test('while the summary is being written it cannot be asked for twice', () => {
    render(<ContextMeter usage={LOW} onStartChatFromSummary={vi.fn()} handoffBusy />);
    fireEvent.click(trigger());
    expect(offer()).toBeDisabled();
    expect(offer()).toHaveTextContent(/writing the summary/i);
  });

  test('Escape closes the menu', () => {
    render(<ContextMeter usage={LOW} onStartChatFromSummary={vi.fn()} />);
    fireEvent.click(trigger());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
