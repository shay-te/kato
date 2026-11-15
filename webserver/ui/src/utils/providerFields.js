// A provider field is a secret (render as a password input, never echo
// its value back) when its key mentions a token / secret / password.
export function isSecretKey(key) {
  const lower = String(key).toLowerCase();
  return lower.includes('token')
    || lower.includes('secret')
    || lower.includes('password');
}

// Build the editable draft for one provider: ``{ fieldKey: currentValue }``.
export function buildDraftFor(providers, name) {
  const fields = (providers?.[name]?.fields) || {};
  const out = {};
  for (const key of Object.keys(fields)) {
    out[key] = fields[key]?.value || '';
  }
  return out;
}

// The hosts the operator set up first (``set_up``, from the server), the
// server's order otherwise. The Git tab always opened on Bitbucket — the first
// in the list — even for someone who only uses Azure DevOps. A server that
// does not say which are set up keeps its own order.
export function orderedProviders(supported, providers) {
  const list = Array.isArray(supported) ? supported : [];
  const isSetUp = (name) => providers?.[name]?.set_up === true;
  return [...list.filter(isSetUp), ...list.filter((name) => !isSetUp(name))];
}

// "Azure DevOps — set up" / "Bitbucket — not set up"; the bare name when the
// server does not say.
export function providerOptionLabel(name, labels, providers) {
  const label = labels?.[name] || name;
  const setUp = providers?.[name]?.set_up;
  if (typeof setUp !== 'boolean') { return label; }
  return `${label} — ${setUp ? 'set up' : 'not set up'}`;
}

// A tracker's workflow-state fields (the field and state kato moves a ticket
// to on start, review and done): optional, so the form folds them away.
const WORKFLOW_FIELD = /_(PROGRESS|REVIEW|DONE)_STATE(_FIELD)?$/;

export function isWorkflowField(key) {
  return WORKFLOW_FIELD.test(String(key || ''));
}

// The server's ``order`` (most needed first) — JSON sorts an object's keys,
// so the fields themselves arrive alphabetical. Keys the order does not name
// follow in their own order; names the fields do not have are dropped.
export function orderedFieldKeys(order, keys) {
  const present = Array.isArray(keys) ? keys : [];
  const named = (Array.isArray(order) ? order : []).filter((key) => present.includes(key));
  return [...named, ...present.filter((key) => !named.includes(key))];
}

// A refused connection kato could say how to fix, as a field to fill in: a
// Jira token WITH scopes works only through Atlassian's gateway, and kato's
// check names the exact gateway address (jira_client.SCOPED_TOKEN_MESSAGE) —
// no one should have to copy it out of an error by hand.
const JIRA_GATEWAY_ADDRESS = /https:\/\/api\.atlassian\.com\/ex\/jira\/[0-9a-f-]{36}/i;

export function connectionFixes(provider, errorText) {
  if (provider !== 'jira') { return []; }
  const match = JIRA_GATEWAY_ADDRESS.exec(String(errorText || ''));
  return match ? [{ key: 'JIRA_API_BASE_URL', value: match[0], label: 'Use this address' }] : [];
}
