// Tests for the text-file attachment path (composer inlines data/config/text
// files instead of rejecting them as "not an image").

import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';

import {
  looksLikeTextFile,
  readTextAttachment,
  formatTextAttachment,
  formatSavedAttachment,
  humanSize,
  TEXT_REJECT_REASON,
  MAX_TEXT_ATTACHMENT_BYTES,
} from './textAttachment.js';


// A FileReader shim whose ``readAsText`` returns a preset string. The
// production ``readSliceAsText`` calls ``file.slice(...)`` then reads it; our
// fake file's slice returns itself carrying the text.
function _installFileReaderShim() {
  globalThis.FileReader = class {
    constructor() { this.onload = null; this.onerror = null; this.result = ''; }
    readAsText(blob) {
      Promise.resolve().then(() => {
        if (blob && blob.__forceError) {
          if (this.onerror) { this.onerror(); }
          return;
        }
        this.result = blob.__text || '';
        if (this.onload) { this.onload(); }
      });
    }
  };
}

afterEach(function () { delete globalThis.FileReader; });

function _file({ name = 'a.txt', type = '', size = 10, text = 'hello', forceError = false } = {}) {
  return {
    name,
    type,
    size,
    __text: text,
    __forceError: forceError,
    slice() { return this; },
  };
}


test('looksLikeTextFile: images are NOT text', function () {
  assert.equal(looksLikeTextFile({ name: 'x.png', type: 'image/png' }), false);
  assert.equal(looksLikeTextFile({ name: 'x.webp', type: 'image/webp' }), false);
});

test('looksLikeTextFile: json / xml / yaml by extension or mime', function () {
  assert.ok(looksLikeTextFile({ name: 'data.json', type: 'application/json' }));
  assert.ok(looksLikeTextFile({ name: 'feed.xml', type: '' }));
  assert.ok(looksLikeTextFile({ name: 'conf.yaml', type: '' }));
  assert.ok(looksLikeTextFile({ name: 'x', type: 'application/ld+json' }));
});

test('looksLikeTextFile: an EXTENSIONLESS, no-mime file is treated as a text candidate', function () {
  // The operator's "text file with no extension" — sniffed later in read.
  assert.ok(looksLikeTextFile({ name: 'LICENSE', type: '' }));
  assert.ok(looksLikeTextFile({ name: 'notes', type: '' }));
});

test('looksLikeTextFile: dotfiles like .gitignore are text', function () {
  assert.ok(looksLikeTextFile({ name: '.gitignore', type: '' }));
});

test('looksLikeTextFile: a known-binary extension is NOT text', function () {
  assert.equal(looksLikeTextFile({ name: 'a.pdf', type: 'application/pdf' }), false);
  assert.equal(looksLikeTextFile({ name: 'a.zip', type: '' }), false);
});

test('readTextAttachment: reads a small text file', async function () {
  _installFileReaderShim();
  const { text, truncated, reason } = await readTextAttachment(
    _file({ name: 'a.json', text: '{"a":1}', size: 7 }),
  );
  assert.equal(reason, '');
  assert.equal(text, '{"a":1}');
  assert.equal(truncated, false);
});

test('readTextAttachment: flags truncation past the size cap', async function () {
  _installFileReaderShim();
  const { text, truncated } = await readTextAttachment(
    _file({ name: 'big.log', text: 'x', size: MAX_TEXT_ATTACHMENT_BYTES + 1 }),
  );
  assert.equal(text, 'x');
  assert.equal(truncated, true);
});

test('readTextAttachment: rejects a binary blob (NUL bytes)', async function () {
  _installFileReaderShim();
  const { text, reason } = await readTextAttachment(
    _file({ name: 'mystery', text: 'abc\u0000\u0001\u0002def', size: 8 }),
  );
  assert.equal(text, null);
  assert.equal(reason, TEXT_REJECT_REASON.BINARY);
});

test('readTextAttachment: rejects an empty file', async function () {
  _installFileReaderShim();
  const { text, reason } = await readTextAttachment(
    _file({ name: 'empty.txt', text: '   \n', size: 4 }),
  );
  assert.equal(text, null);
  assert.equal(reason, TEXT_REJECT_REASON.EMPTY);
});

test('formatTextAttachment: labelled, fenced, with a language hint', function () {
  const out = formatTextAttachment('data.json', '{"a":1}');
  assert.ok(out.startsWith('Attached `data.json`:'));
  assert.ok(out.includes('```json\n{"a":1}\n```'));
});

test('formatTextAttachment: escapes content that itself contains a fence', function () {
  // Content with ``` must be wrapped in a LONGER fence so it can't break out.
  const out = formatTextAttachment('readme.md', 'text\n```\ncode\n```\nmore');
  assert.ok(out.includes('````'));
  // The inner triple-fence survives verbatim.
  assert.ok(out.includes('```\ncode\n```'));
});

test('formatTextAttachment: notes truncation', function () {
  const out = formatTextAttachment('big.log', 'partial', { truncated: true });
  assert.ok(/truncated to first \d+ KB/.test(out));
});

// A file too big to inline is written into the task workspace instead, and
// the prompt carries its PATH. Truncating was the old behaviour: the agent
// received a log whose interesting part had been cut off, with only a toast
// to say so.
test('formatSavedAttachment: hands the agent the path, not the content', function () {
  const out = formatSavedAttachment(
    'messaging_js_logs.txt', '/ws/UNA-1/attachments/messaging_js_logs.txt', 5 * 1024 * 1024,
  );
  assert.ok(out.includes('`/ws/UNA-1/attachments/messaging_js_logs.txt`'));
  assert.ok(out.includes('messaging_js_logs.txt'));
  // Says how big it is, so the agent can judge how to read it.
  assert.ok(out.includes('5.0 MB'));
  // And tells it to read from disk rather than ask for a paste.
  assert.ok(/read or search that file/i.test(out));
  // Never the word "truncated" — nothing was cut off.
  assert.ok(!/truncated/i.test(out));
});

test('formatSavedAttachment: scales the size unit', function () {
  assert.ok(formatSavedAttachment('a.txt', '/p', 900).includes('900 bytes'));
  assert.ok(formatSavedAttachment('a.txt', '/p', 4096).includes('4 KB'));
});

test('formatSavedAttachment: falls back when the name is blank', function () {
  assert.ok(formatSavedAttachment('', '/p', 10).includes('`attachment`'));
});

// Exported so the New Task pane's attachment chips (newTask/NewTaskAttachments.jsx)
// share one size formatter instead of re-implementing it.
test('humanSize: bytes, KB, MB, and a non-numeric input', function () {
  assert.equal(humanSize(42), '42 bytes');
  assert.equal(humanSize(2048), '2 KB');
  assert.equal(humanSize(5 * 1024 * 1024), '5.0 MB');
  assert.equal(humanSize(undefined), '0 bytes');
});
