// The handlers App gives every tab must keep their identity.
//
// A new function on each render is a changed prop on every tab, which silently
// defeats ``memo(Tab)``: nothing breaks, the tabs just go back to redrawing on
// everything. It happened twice while this was being fixed, and only a
// measurement in a real browser showed it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const source = readFileSync(join(new URL('.', import.meta.url).pathname, 'App.jsx'), 'utf8');

for (const name of ['selectTaskAndReveal', 'requestForgetTask']) {
  test(`${name} has no dependencies`, () => {
    const start = source.indexOf(`const ${name} = useCallback(`);
    assert.notEqual(start, -1, `${name} is no longer a useCallback`);
    const end = source.indexOf('\n  }, ', start);
    assert.equal(source.slice(end, end + 11), '\n  }, []);\n');
  });
}
