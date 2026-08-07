// Tests for copyTextToClipboard — specifically the webview-focus fallback:
// the async Clipboard API rejects when the document isn't focused (common in
// a VSCode webview on the FIRST click), so we must fall back to execCommand
// instead of silently doing nothing.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { copyFileName, copyTextToClipboard } from './clipboard.js';

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

describe('copyFileName', () => {
  test('copies only the last segment of a path', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);

    await copyFileName('kato_core_lib/data_layers/service/agent_service.py');
    expect(writeText).toHaveBeenCalledWith('agent_service.py');
  });

  test('a bare name copies unchanged', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);

    await copyFileName('README.md');
    expect(writeText).toHaveBeenCalledWith('README.md');
  });

  test('a folder path copies the folder name, not an empty string', async () => {
    // A tree folder row's relative path can carry a trailing separator;
    // basenameOf strips it, so the operator gets ``service`` rather than
    // a silent no-op copy.
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);

    await copyFileName('kato_core_lib/data_layers/service/');
    expect(writeText).toHaveBeenCalledWith('service');
  });

  test('empty path is a no-op', async () => {
    const writeText = vi.fn();
    setClipboard(writeText);

    await copyFileName('');
    expect(writeText).not.toHaveBeenCalled();
  });
});
