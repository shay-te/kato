// Tests for ChatCostDot — the traffic light for what a chat costs per turn.
//
// It exists because the context meter cannot answer this: "% left" reports a
// healthy 51% for a chat re-reading 490k tokens on every single turn. The two
// indicators sit next to each other and must stay independent.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import ChatCostDot from './ChatCostDot.jsx';
import { MODERATE_MULTIPLE, EXPENSIVE_MULTIPLE } from '../utils/chatCost.js';

function _dot(container) {
  return container.querySelector('.chat-cost-dot');
}

function _render(used, baseline) {
  return render(
    <ChatCostDot usage={{ used_tokens: used, baseline_tokens: baseline }} />,
  ).container;
}

describe('ChatCostDot', () => {

  test('green while the chat costs about what a fresh one would', () => {
    expect(_dot(_render(45_000, 40_000)).className)
      .toContain('chat-cost-dot--safe');
  });

  test('yellow from the moderate threshold', () => {
    expect(_dot(_render(40_000 * MODERATE_MULTIPLE, 40_000)).className)
      .toContain('chat-cost-dot--moderate');
  });

  test('red from the expensive threshold', () => {
    expect(_dot(_render(40_000 * EXPENSIVE_MULTIPLE, 40_000)).className)
      .toContain('chat-cost-dot--expensive');
  });

  test('an unknown cost shows NO dot — never a green one', () => {
    // A green light nobody earned is worse than no light: it says "carry on"
    // about a chat whose cost we cannot see.
    expect(_dot(_render(490_000, 0))).toBeNull();
    expect(_dot(_render(0, 40_000))).toBeNull();
    expect(_dot(render(<ChatCostDot usage={null} />).container)).toBeNull();
    expect(_dot(render(<ChatCostDot />).container)).toBeNull();
  });

  test('junk values show nothing rather than a wrong colour', () => {
    expect(_dot(_render('lots', 'some'))).toBeNull();
    expect(_dot(_render(NaN, 40_000))).toBeNull();
  });

  test('red says what to do, and that a new chat is safe to start', () => {
    const dot = _dot(_render(490_000, 40_000));
    const tooltip = dot.getAttribute('data-tooltip');
    expect(tooltip).toMatch(/490k\/turn/);
    expect(tooltip).toMatch(/12x a fresh chat/);
    expect(tooltip).toMatch(/Start a new chat/);
    // The hesitation it has to answer: "will I lose my work?"
    expect(tooltip).toMatch(/work is untouched/);
  });

  test('the tooltip stays short enough not to be clipped', () => {
    // It hangs off a 9px dot in the corner of a pane that can be narrow, and
    // a CSS tooltip cannot reflow away from an edge — it just gets sliced by
    // the first ancestor that hides overflow. Length is the only control.
    for (const [used, base] of [[45_000, 40_000], [240_000, 40_000], [490_000, 40_000]]) {
      const tooltip = _dot(_render(used, base)).getAttribute('data-tooltip');
      expect(tooltip.length).toBeLessThanOrEqual(90);
    }
  });

  test('green explains that there is nothing to do', () => {
    expect(_dot(_render(45_000, 40_000)).getAttribute('data-tooltip'))
      .toMatch(/Nothing to do/);
  });

  test('the tooltip opens upward and right-aligned', () => {
    // It lives in the bottom-right corner of the composer; the default
    // (below, centred) tooltip renders under the chat input and off-screen.
    const dot = _dot(_render(490_000, 40_000));
    expect(dot.className).toContain('tooltip-above');
    expect(dot.className).toContain('tooltip-end');
  });

  test('it is announced to screen readers, not just coloured', () => {
    _render(490_000, 40_000);
    expect(screen.getByRole('status')).toHaveAttribute(
      'aria-label', expect.stringMatching(/expensive/),
    );
  });
});
