import { test } from 'node:test';
import assert from 'node:assert/strict';

import { placeHoverCard } from './hoverCardPlacement.js';

const SCREEN = { width: 1000, height: 800 };
const CARD = { width: 300, height: 200 };
const rect = (left, top, width = 100, height = 30) => ({
  left, top, right: left + width, bottom: top + height,
});

test('beside: right of the row, its top lined up with the row', () => {
  assert.deepEqual(placeHoverCard(rect(10, 100), CARD, SCREEN), { top: 100, left: 118, placement: 'right' });
});

test('beside: flipped left when it would run off the right edge', () => {
  assert.deepEqual(placeHoverCard(rect(650, 100), CARD, SCREEN), { top: 100, left: 342, placement: 'left' });
});

test('beside: moved up to stay inside the bottom, never above the top', () => {
  assert.deepEqual(placeHoverCard(rect(10, 700), CARD, SCREEN), { top: 592, left: 118, placement: 'right' });
  assert.deepEqual(placeHoverCard(rect(10, 0), { width: 300, height: 900 }, SCREEN).top, 8);
});

test('below: under the tab, its left lined up with the tab', () => {
  assert.deepEqual(placeHoverCard(rect(200, 40), CARD, SCREEN, 'below'), { top: 78, left: 200, placement: 'below' });
});

test('below: flipped above when there is no room under it', () => {
  assert.deepEqual(placeHoverCard(rect(200, 700), CARD, SCREEN, 'below'), { top: 492, left: 200, placement: 'above' });
});

test('below: moved left to stay inside the right edge, never past the left one', () => {
  assert.deepEqual(placeHoverCard(rect(900, 40), CARD, SCREEN, 'below').left, 692);
  assert.deepEqual(placeHoverCard(rect(-50, 40), CARD, SCREEN, 'below').left, 8);
});
