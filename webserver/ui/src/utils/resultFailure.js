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

export function resultFailureText(raw) {
  const event = raw || {};
  const result = text(event.result);
  if (result) { return result; }
  const errors = (Array.isArray(event.errors) ? event.errors : []).map(text).filter(Boolean);
  if (errors.length) { return errors.join('\n'); }
  // The agent's own last error output, attached by the server.
  return text(event.failure_detail) || STOPPED_BY_SUBTYPE[event.subtype] || NO_REASON;
}
