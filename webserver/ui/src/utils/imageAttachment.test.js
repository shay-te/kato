import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';

import {
  collectImageParts,
  fileToImagePart,
  IMAGE_REJECT_REASON,
} from './imageAttachment.js';


// Minimal Blob/FileReader shim. The real browser FileReader reads a
// Blob as a base64 data URL via readAsDataURL. We stub a deterministic
// reader so the tests don't depend on a DOM.
function _installFileReaderShim() {
  globalThis.FileReader = class {
    constructor() {
      this.onload = null;
      this.onerror = null;
      this.result = '';
    }
    readAsDataURL(blob) {
      // Resolve on next tick so the await in production code works.
      Promise.resolve().then(() => {
        if (blob && blob.__forceError) {
          if (this.onerror) this.onerror();
          return;
        }
        this.result = `data:${blob.type};base64,${blob.__base64 || ''}`;
        if (this.onload) this.onload();
      });
    }
  };
}

afterEach(function () {
  delete globalThis.FileReader;
});


function _blob({ type = 'image/png', size = 100, base64 = 'AAAA', forceError = false } = {}) {
  // Need ``instanceof Blob`` to be true (collectImageParts checks)
  // and custom ``type``/``size`` (so we can fake a 6 MB blob without
  // allocating one). Blob's ``type`` and ``size`` are getters on the
  // prototype, so Object.defineProperty on the instance overrides
  // them.
  const blob = Object.create(Blob.prototype);
  Object.defineProperty(blob, 'type', { value: type });
  Object.defineProperty(blob, 'size', { value: size });
  blob.__base64 = base64;
  blob.__forceError = forceError;
  return blob;
}


test('fileToImagePart returns base64 data and media type for a PNG', async function () {
  _installFileReaderShim();
  const result = await fileToImagePart(_blob({ type: 'image/png', base64: 'PNGDATA' }));
  assert.equal(result.reason, '');
  assert.equal(result.part.media_type, 'image/png');
  assert.equal(result.part.data, 'PNGDATA');
});

test('fileToImagePart rejects unsupported media types', async function () {
  _installFileReaderShim();
  const result = await fileToImagePart(_blob({ type: 'image/tiff' }));
  assert.equal(result.part, null);
  assert.equal(result.reason, IMAGE_REJECT_REASON.UNSUPPORTED_TYPE);
});

test('fileToImagePart rejects files over the 5 MB cap', async function () {
  _installFileReaderShim();
  const result = await fileToImagePart(
    _blob({ type: 'image/png', size: 6 * 1024 * 1024 }),
  );
  assert.equal(result.part, null);
  assert.equal(result.reason, IMAGE_REJECT_REASON.TOO_LARGE);
});

test('fileToImagePart returns read_failed when the reader errors', async function () {
  _installFileReaderShim();
  const result = await fileToImagePart(_blob({ forceError: true }));
  assert.equal(result.part, null);
  assert.equal(result.reason, IMAGE_REJECT_REASON.READ_FAILED);
});

test('fileToImagePart returns read_failed for null input', async function () {
  _installFileReaderShim();
  const result = await fileToImagePart(null);
  assert.equal(result.part, null);
  assert.equal(result.reason, IMAGE_REJECT_REASON.READ_FAILED);
});

test('collectImageParts walks an array of blobs and aggregates parts', async function () {
  _installFileReaderShim();
  const { parts, rejections } = await collectImageParts([
    _blob({ type: 'image/png', base64: 'A' }),
    _blob({ type: 'image/jpeg', base64: 'B' }),
  ]);
  assert.equal(parts.length, 2);
  assert.equal(parts[0].media_type, 'image/png');
  assert.equal(parts[1].media_type, 'image/jpeg');
  assert.equal(rejections.length, 0);
});

test('collectImageParts surfaces rejections without stopping', async function () {
  _installFileReaderShim();
  const { parts, rejections } = await collectImageParts([
    _blob({ type: 'image/tiff' }),
    _blob({ type: 'image/png', base64: 'OK' }),
  ]);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].data, 'OK');
  assert.equal(rejections.length, 1);
  assert.equal(rejections[0].reason, IMAGE_REJECT_REASON.UNSUPPORTED_TYPE);
});

test('collectImageParts respects existingCount when capping the per-message total', async function () {
  _installFileReaderShim();
  // 8 already attached, 5 more pasted → only 2 fit (cap is 10).
  const items = Array.from({ length: 5 }, (_, i) =>
    _blob({ type: 'image/png', base64: String(i) }));
  const { parts, rejections } = await collectImageParts(items, { existingCount: 8 });
  assert.equal(parts.length, 2);
  assert.equal(rejections.length, 1);
  assert.equal(rejections[0].reason, IMAGE_REJECT_REASON.TOO_MANY);
});

test('collectImageParts unwraps DataTransferItem-like objects via getAsFile', async function () {
  _installFileReaderShim();
  const blob = _blob({ type: 'image/png', base64: 'X' });
  const item = { getAsFile: () => blob };
  const { parts } = await collectImageParts([item]);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].data, 'X');
});
