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
