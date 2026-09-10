// ``scrollElementToTop`` — put an element's flow position at the top of its
// scroller. Written for the chat's jump-to-start target.
//
// The FIRST version of this walked ``offsetTop``/``offsetParent`` and had a
// green unit test built from fake ``{offsetTop, offsetParent}`` objects. The
// button did nothing in a browser, and the test could never have caught it:
// jsdom reports ``offsetParent`` as null for every element, so the fakes were
// testing arithmetic that the real DOM never performs. These tests drive real
// elements with stubbed RECTS — the same numbers a browser reports.

import { describe, test, expect } from 'vitest';
import { scrollElementToTop } from './scrollUtils.js';

function withRect(el, top) {
  el.getBoundingClientRect = () => ({ top, bottom: top + 10, height: 10, left: 0, right: 0, width: 0 });
  return el;
}

function scroller(scrollTop, top = 0) {
  const el = withRect(document.createElement('div'), top);
  el.scrollTop = scrollTop;
  return el;
}

describe('scrollElementToTop', () => {
  test('scrolls down by the gap between the element and the scroller top', () => {
    const box = scroller(0, 100);
    const target = withRect(document.createElement('div'), 940);
    scrollElementToTop(target, box);
    expect(box.scrollTop).toBe(840);
  });

  test('scrolls UP when the element is above the viewport', () => {
    // The real case: the operator has read past the prompt, so the turn's
    // rect is negative relative to the scroller.
    const box = scroller(2000, 100);
    const target = withRect(document.createElement('div'), -600);
    scrollElementToTop(target, box);
    expect(box.scrollTop).toBe(1300);
  });

  test('works on an element already at the top (a no-op, not a jump)', () => {
    const box = scroller(500, 100);
    scrollElementToTop(withRect(document.createElement('div'), 100), box);
    expect(box.scrollTop).toBe(500);
  });

  test('honours an offset', () => {
    const box = scroller(0, 0);
    scrollElementToTop(withRect(document.createElement('div'), 500), box, { offset: 20 });
    expect(box.scrollTop).toBe(480);
  });

  test('never scrolls negative', () => {
    const box = scroller(0, 0);
    scrollElementToTop(withRect(document.createElement('div'), -900), box);
    expect(box.scrollTop).toBe(0);
  });

  test('tolerates a missing node, scroller, or unmeasurable element', () => {
    expect(() => scrollElementToTop(null, scroller(0))).not.toThrow();
    expect(() => scrollElementToTop(document.createElement('div'), null)).not.toThrow();
    // A bare object with no rect API — never throw inside a click handler.
    expect(() => scrollElementToTop({}, scroller(0))).not.toThrow();
  });
});
