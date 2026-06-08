import { useState } from 'react';
import { copyTextToClipboard } from '../utils/clipboard.js';

// Shared copy-to-clipboard behaviour for the chat's copy buttons (turn
// response, fenced code block, tool-output block). One place for the
// copied-state flash + the clipboard call (with its webview-focus fallback),
// so the three buttons don't each re-implement it.
//
// Usage: const { copied, copy } = useCopyAction();
//        <button onClick={(e) => copy(theText, e)}>…</button>
export function useCopyAction(resetMs = 1500) {
  const [copied, setCopied] = useState(false);
  async function copy(text, event) {
    event?.stopPropagation?.();
    const value = String(text || '');
    if (!value) { return; }
    try {
      await copyTextToClipboard(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), resetMs);
    } catch (_) { /* clipboard unavailable — leave the button idle */ }
  }
  return { copied, copy };
}
