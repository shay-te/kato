// The chat reads none of the fields the server trims from replayed history.
//
// ``_history_raw_for_chat`` in webserver/kato_webserver/app.py drops fields of
// every replayed transcript record before it is sent: a long chat's replay was
// 14.6 MB on every connect, and more than half of it was fields nothing in this
// UI touched. The risk of trimming is a later feature that starts reading one of
// them and silently gets nothing from history — so this fails first. Take the
// field off the server's list, then read it here.
//
// ``cwd``, ``slug`` and ``usage`` are trimmed too but are too common as words to
// scan for; the token-count names below stand in for ``message.usage``.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('.', import.meta.url));
const TRIMMED_FIELDS = [
  'toolUseResult', 'parentUuid', 'gitBranch', 'userType', 'promptId',
  'sourceToolAssistantUUID', 'isSidechain',
  'input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens',
];

function uiSourceFiles(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__fixtures__') { continue; }
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      files.push(...uiSourceFiles(path));
    } else if (/\.(js|jsx)$/.test(name) && !/\.test\.(js|jsx)$/.test(name)) {
      files.push(path);
    }
  }
  return files;
}

test('no UI source reads a field the server trims from replayed history', () => {
  const offenders = [];
  for (const file of uiSourceFiles(SRC)) {
    const text = readFileSync(file, 'utf8');
    for (const field of TRIMMED_FIELDS) {
      if (new RegExp(`\\b${field}\\b`).test(text)) {
        offenders.push(`${file.slice(SRC.length)} reads ${field}`);
      }
    }
  }
  assert.deepEqual(offenders, [], 'the server strips these from replayed history — see _history_raw_for_chat');
});

test('the scan actually reads the UI source', () => {
  // A scan over an empty or moved directory would pass while checking nothing.
  assert.ok(uiSourceFiles(SRC).length > 100);
});
