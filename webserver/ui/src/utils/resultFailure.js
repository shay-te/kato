// Why a turn failed, in the best words there are. A failed result carries its
// reason in ``result``, or, when it failed during execution, in ``errors`` with
// no ``result`` at all. Only ``result`` was read, so that case showed the single
// word "failed" with the reason sitting unread on the same event.
const STOPPED_BY_SUBTYPE = Object.freeze({
  error_during_execution: 'The agent stopped with an error while working, and gave no reason.',
  error_max_turns: 'The agent reached its limit of turns.',
  error_max_budget_usd: 'The agent reached its spending limit.',
});

const NO_REASON = 'The agent stopped with an error and gave no reason.';

function text(value) {
  return String(value ?? '').trim();
}

// A turn limit is never kato's default: it is there because one was set, and
// "Reached maximum number of turns (25)" alone did not say where.
const MAX_TURNS_HINT = 'Kato stops a reply after that many turns only when a limit is set: '
  + 'Settings → General → Max turns (empty = no limit).';

export function resultFailureText(raw) {
  const event = raw || {};
  const reason = failureReason(event);
  return event.subtype === 'error_max_turns' ? `${reason}\n${MAX_TURNS_HINT}` : reason;
}

function failureReason(event) {
  const result = text(event.result);
  if (result) { return result; }
  const errors = (Array.isArray(event.errors) ? event.errors : []).map(text).filter(Boolean);
  if (errors.length) { return errors.join('\n'); }
  // The agent's own last error output, attached by the server.
  return text(event.failure_detail) || STOPPED_BY_SUBTYPE[event.subtype] || NO_REASON;
}

const MAX_REFUSALS_LISTED = 5;

// The tool calls the CLI refused WITHOUT asking the operator, from the turn's
// ``permission_denials``: a background task cannot ask, and a remembered
// "Deny always" answers for you. The agent read each refusal — and often
// retried until it ran out of turns — while the chat showed nothing. The
// report: "an error about permission to edit a file, but it never asked".
// '' = none.
export function refusedToolsText(raw) {
  const denials = Array.isArray(raw?.permission_denials) ? raw.permission_denials : [];
  const counts = new Map();
  for (const denial of denials) {
    const name = describeDenial(denial);
    if (name) { counts.set(name, (counts.get(name) || 0) + 1); }
  }
  if (!counts.size) { return ''; }
  const total = [...counts.values()].reduce((sum, n) => sum + n, 0);
  const lines = [...counts.entries()].slice(0, MAX_REFUSALS_LISTED)
    .map(([name, n]) => `• ${name}${n > 1 ? ` ×${n}` : ''}`);
  if (counts.size > MAX_REFUSALS_LISTED) { lines.push(`• …and ${counts.size - MAX_REFUSALS_LISTED} more`); }
  return [
    `Refused without asking you (${total}):`,
    ...lines,
    'A background task cannot ask you, and a remembered “Deny always” answers for you.',
  ].join('\n');
}

// "Edit src/app.ts", "Bash npm test", or just the tool's name.
function describeDenial(denial) {
  const name = text(denial?.tool_name);
  if (!name) { return ''; }
  const input = denial?.tool_input || {};
  const target = text(input.file_path || input.notebook_path || input.path || input.command);
  if (!target) { return name; }
  return `${name} ${target.length > 80 ? `${target.slice(0, 79)}…` : target}`;
}
