// A styles-only rebuild is swapped in place; anything else is not.

import test from 'node:test';
import assert from 'node:assert/strict';
import { onlyStylesChanged } from './stylesheetSwap.js';

test('only the stylesheet stamps moved: a swap, no reload', () => {
  assert.equal(onlyStylesChanged('100-200-300', '100-201-300'), true);
  assert.equal(onlyStylesChanged('100-200-300', '100-200-301'), true);
  assert.equal(onlyStylesChanged('100-200-300', '101-200-300'), false);  // the script changed
  assert.equal(onlyStylesChanged('100-200-300', '100-200-300'), false);  // nothing changed
  assert.equal(onlyStylesChanged('100', '100-200-300'), false);          // an id from before
  assert.equal(onlyStylesChanged('', ''), false);
  assert.equal(onlyStylesChanged(null, undefined), false);
});
