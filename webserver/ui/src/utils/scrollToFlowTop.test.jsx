// ``scrollToFlowTop`` — jump to an element's FLOW position, not its painted
// one. Written for the chat's "scroll back to where this prompt starts"
// target icon, whose whole problem is that the header it lives on is
// ``position: sticky``.

import { describe, test, expect } from 'vitest';
import { scrollToFlowTop } from './scrollUtils.js';

function fakeNode(offsetTop, offsetParent) {
  return { offsetTop, offsetParent };
}

describe('scrollToFlowTop', () => {
  test('scrolls to the node\'s own offset within the scroller', () => {
    const scroller = { scrollTop: 0 };
    const node = fakeNode(1200, scroller);
    scrollToFlowTop(node, scroller);
    expect(scroller.scrollTop).toBe(1200);
  });

  test('accumulates through a positioned ancestor between the two', () => {
    // A turn wrapper with its own positioning contributes its offset; a
    // single ``node.offsetTop`` read would land hundreds of pixels short.
    const scroller = { scrollTop: 0 };
    const turn = fakeNode(900, scroller);
    const node = fakeNode(40, turn);
    scrollToFlowTop(node, scroller);
    expect(scroller.scrollTop).toBe(940);
  });

  test('honours an offset so the target is not flush against the edge', () => {
    const scroller = { scrollTop: 0 };
    scrollToFlowTop(fakeNode(500, scroller), scroller, { offset: 20 });
    expect(scroller.scrollTop).toBe(480);
  });

  test('never scrolls negative', () => {
    const scroller = { scrollTop: 77 };
    scrollToFlowTop(fakeNode(5, scroller), scroller, { offset: 100 });
    expect(scroller.scrollTop).toBe(0);
  });

  test('does nothing when the offset chain never reaches the scroller', () => {
    // A fixed / detached ancestor. Better to leave the scroll alone than to
    // fling the operator to an arbitrary position.
    const scroller = { scrollTop: 42 };
    const orphan = fakeNode(10, null);
    scrollToFlowTop(orphan, scroller);
    expect(scroller.scrollTop).toBe(42);
  });

  test('tolerates a missing node or scroller', () => {
    expect(() => scrollToFlowTop(null, { scrollTop: 0 })).not.toThrow();
    expect(() => scrollToFlowTop({ offsetTop: 1 }, null)).not.toThrow();
  });
});
