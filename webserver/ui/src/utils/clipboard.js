import { toast } from '../stores/toastStore.js';
import { formatRepoRelativePath } from '../diffModel.js';
import { basenameOf } from './basenameOf.js';

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

// Copy → success toast → failure toast. The public copy actions below
// differ only in WHAT they copy and how the success toast is titled, so
// the sequence lives here once instead of in each of them.
async function copyWithToast(text, successTitle) {
  if (!text) { return; }
  try {
    await copyTextToClipboard(text);
    toast.show({
      kind: 'success',
      title: successTitle,
      message: text,
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

// Copy a repo-relative file path to the clipboard and surface a toast
// for the success / failure outcome. Shared by the Files tab path menu
// and the diff-file header path menu, which previously hand-rolled the
// identical formatRepoRelativePath -> copy -> toast sequence.
export async function copyRepoRelativePath(repoId, path) {
  if (!path) { return; }
  await copyWithToast(formatRepoRelativePath(repoId, path), 'Copied relative path');
}

// Just the last segment — ``agent_service.py``, not
// ``kato_core_lib/data_layers/service/agent_service.py``. What you want
// when pasting a name into a search box, a ticket, or a message, where
// the surrounding path is noise. Works for folders too (the folder's own
// name), since a folder row's relative path ends in that name.
export async function copyFileName(path) {
  await copyWithToast(basenameOf(String(path || '').trim()), 'Copied file name');
}
