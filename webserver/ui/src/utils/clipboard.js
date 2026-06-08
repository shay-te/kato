import { toast } from '../stores/toastStore.js';
import { formatRepoRelativePath } from '../diffModel.js';

export async function copyTextToClipboard(text) {
  const value = String(text || '');
  if (!value) { return; }
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch (_) {
      // The async Clipboard API rejects when the document isn't focused
      // (common in a VSCode webview on the FIRST click — the click focuses
      // the view, so a retry would work) or when permission is denied. Fall
      // through to the execCommand path, which doesn't need focus, so the
      // first click copies instead of silently doing nothing.
    }
  }
  if (typeof document === 'undefined') {
    throw new Error('clipboard unavailable');
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand('copy');
  textarea.remove();
  if (!copied) {
    throw new Error('clipboard unavailable');
  }
}

// Copy a repo-relative file path to the clipboard and surface a toast
// for the success / failure outcome. Shared by the Files tab path menu
// and the diff-file header path menu, which previously hand-rolled the
// identical formatRepoRelativePath -> copy -> toast sequence.
export async function copyRepoRelativePath(repoId, path) {
  const repoPath = formatRepoRelativePath(repoId, path);
  if (!path) { return; }
  try {
    await copyTextToClipboard(repoPath);
    toast.show({
      kind: 'success',
      title: 'Copied relative path',
      message: repoPath,
      durationMs: 2500,
    });
  } catch (err) {
    toast.show({
      kind: 'error',
      title: 'Copy failed',
      message: String(err?.message || err || 'clipboard unavailable'),
      durationMs: 5000,
    });
  }
}
