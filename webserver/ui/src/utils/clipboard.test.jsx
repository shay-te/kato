// Tests for copyTextToClipboard — specifically the webview-focus fallback:
// the async Clipboard API rejects when the document isn't focused (common in
// a VSCode webview on the FIRST click), so we must fall back to execCommand
// instead of silently doing nothing.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { copyTextToClipboard } from './clipboard.js';

const origClipboard = navigator.clipboard;
const origExec = document.execCommand;

afterEach(() => {
  Object.defineProperty(navigator, 'clipboard', { value: origClipboard, configurable: true });
  document.execCommand = origExec;
  vi.restoreAllMocks();
});

function setClipboard(writeText) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText }, configurable: true,
  });
}

describe('copyTextToClipboard', () => {
  test('uses the async Clipboard API when it resolves', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec;

    await copyTextToClipboard('hello');
    expect(writeText).toHaveBeenCalledWith('hello');
    expect(exec).not.toHaveBeenCalled(); // no fallback needed
  });

  test('falls back to execCommand when writeText REJECTS (not focused)', async () => {
    const writeText = vi.fn().mockRejectedValue(
      new DOMException('Document is not focused.', 'NotAllowedError'),
    );
    setClipboard(writeText);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec;

    // Must not throw — the fallback copies instead of failing silently.
    await copyTextToClipboard('retry me');
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(exec).toHaveBeenCalledWith('copy');
  });

  test('empty text is a no-op', async () => {
    const writeText = vi.fn();
    setClipboard(writeText);
    await copyTextToClipboard('');
    expect(writeText).not.toHaveBeenCalled();
  });
});
