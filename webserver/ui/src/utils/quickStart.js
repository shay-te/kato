// Quick start's decisions, apart from its drawing (components/QuickStart.jsx):
// what state Claude Code is in, how a folder reads, and what still stands
// between the operator and "Start kato". Plain functions so a node test pins
// each one.

export const CLAUDE_STATE = Object.freeze({
  MISSING: 'missing',        // not installed
  SIGNED_OUT: 'signed-out',  // installed, not signed in
  READY: 'ready',            // installed and signed in
  INSTALLED: 'installed',    // installed; the CLI cannot say whether it is signed in
});

export function claudeState(claude) {
  if (!claude || !claude.installed) { return CLAUDE_STATE.MISSING; }
  if (claude.logged_in === false) { return CLAUDE_STATE.SIGNED_OUT; }
  return claude.logged_in === true ? CLAUDE_STATE.READY : CLAUDE_STATE.INSTALLED;
}

// "Cannot tell" is never read as signed out: the CLI answers that itself at
// the first task, and blocking on a guess would stop a working setup.
export function claudeUsable(claude) {
  const state = claudeState(claude);
  return state === CLAUDE_STATE.READY || state === CLAUDE_STATE.INSTALLED;
}

// "/Users/me/dev" → "~/dev" — how people say it.
export function homeRelative(path, home) {
  const text = String(path || '');
  const base = String(home || '').replace(/[\\/]+$/, '');
  if (!base || !text.startsWith(base)) { return text; }
  const rest = text.slice(base.length);
  if (!rest) { return '~'; }
  return /^[\\/]/.test(rest) ? `~${rest}` : text;
}

export function repositoriesText(count) {
  return `${count} ${count === 1 ? 'repository' : 'repositories'}`;
}

// The folder Start saves, before the operator picks anything: the best one
// found, else the one kato offers to create.
export function initialFolder(screen) {
  const found = Array.isArray(screen?.folders) ? screen.folders : [];
  if (screen?.folder) { return { path: screen.folder, create: false }; }
  if (found.length) { return { path: found[0].path, create: false }; }
  return { path: String(screen?.new_folder || ''), create: true };
}

// Why Start is not offered yet ('' = it is). One reason, the first that holds.
export function startBlocker(checks, folder) {
  if (!checks) { return 'Checking this computer…'; }
  const state = claudeState(checks.claude);
  if (state === CLAUDE_STATE.MISSING) { return 'Install Claude Code first.'; }
  if (state === CLAUDE_STATE.SIGNED_OUT) { return 'Sign in to Claude first.'; }
  if (!checks.git?.installed) { return 'Install git first.'; }
  if (!String(folder?.path || '').trim()) { return 'Pick the folder your code is in.'; }
  return '';
}

// Everything is in place: nothing left to keep checking.
export function allChecksPass(checks) {
  return Boolean(checks) && claudeUsable(checks.claude) && Boolean(checks.git?.installed);
}
