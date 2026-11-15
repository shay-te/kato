// Quick start's decisions, apart from its drawing (components/QuickStart.jsx):
// which agents there are, what state the picked one is in, how a folder reads,
// and what still stands between the operator and "Start kato". Plain functions
// so a node test pins each one.
//
// Nothing is decided FOR the operator: no agent and no folder is picked until
// they pick one (or picked one on an earlier run).

export const QUICK_START_AGENTS = Object.freeze([
  { id: 'claude', label: 'Claude Code' },
  { id: 'codex', label: 'Codex' },
  { id: 'grok', label: 'Grok' },
  { id: 'opencode', label: 'OpenCode' },
]);

export function agentLabel(id) {
  return QUICK_START_AGENTS.find((agent) => agent.id === id)?.label || String(id || '');
}

export const AGENT_STATE = Object.freeze({
  MISSING: 'missing',        // not installed
  SIGNED_OUT: 'signed-out',  // installed, and it says it is not signed in (Claude Code only)
  READY: 'ready',            // installed and signed in
  INSTALLED: 'installed',    // installed; it cannot say whether it is signed in
});

export function agentState(status) {
  if (!status || !status.installed) { return AGENT_STATE.MISSING; }
  if (status.logged_in === false) { return AGENT_STATE.SIGNED_OUT; }
  return status.logged_in === true ? AGENT_STATE.READY : AGENT_STATE.INSTALLED;
}

// "Cannot tell" is never read as signed out: the CLI answers that itself at
// the first message, and blocking on a guess would stop a working setup.
export function agentUsable(status) {
  const state = agentState(status);
  return state === AGENT_STATE.READY || state === AGENT_STATE.INSTALLED;
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

// The folder already chosen on an earlier run, or none: the operator picks.
export function initialFolder(screen) {
  return screen?.folder ? { path: screen.folder, create: false } : null;
}

// Why Start is not offered yet ('' = it is). One reason, the first that holds.
// Signing in is NOT one: the operator may sign in later — kato's main screen
// asks again (ClaudeSignInBanner) — so a signed-out agent never holds kato back.
export function startBlocker({ agent, checks, folder }) {
  if (!agent) { return 'Pick the agent you use.'; }
  if (!checks) { return 'Checking this computer…'; }
  if (agentState(checks.agent) === AGENT_STATE.MISSING) { return `Install ${agentLabel(agent)} first.`; }
  if (!checks.git?.installed) { return 'Install git first.'; }
  if (!String(folder?.path || '').trim()) { return 'Pick the folder your code is in.'; }
  return '';
}

// Everything checked is in place: nothing left to ask again.
export function allChecksPass(checks) {
  return Boolean(checks) && agentUsable(checks.agent) && Boolean(checks.git?.installed);
}

// Whether the main screen asks about Claude Code's sign-in (ClaudeSignInBanner):
// out of setup, and Claude Code is the agent in front — the open task's, else
// the one new tasks start on ('' = the default, Claude Code).
export function claudeSignInNeeded(configStatus, activeBackend = '') {
  if (!configStatus || configStatus.setup_mode) { return false; }
  return (activeBackend || configStatus.chat_agent || 'claude') === 'claude';
}

// kato's config check says "missing required agent env var: JIRA_API_TOKEN";
// the screen says which setting, and the variable it is kept under.
export function missingSettingText(problem) {
  const text = String(problem || '').trim();
  const match = /^missing required (?:agent )?env var: (\S+)$/i.exec(text);
  return match ? `${match[1]} (not set)` : text;
}
