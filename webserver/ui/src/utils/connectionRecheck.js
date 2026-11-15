// What kato's apply-and-recheck answered (connection_recheck.py), in words
// for the Settings tab that asked — the banner itself follows the outcome on
// its own (the one connection status, polled with the config status).
export const APPLY_KIND = Object.freeze({ OK: 'ok', NOTE: 'note', ERROR: 'error' });

export function recheckOutcome(result) {
  if (!result?.ok) {
    return { kind: APPLY_KIND.ERROR, text: `Kato could not be asked: ${result?.body?.error || result?.error || 'no answer'}` };
  }
  const body = result.body || {};
  if (body.status === 'restarting') {
    return { kind: APPLY_KIND.OK, text: 'Kato is restarting to use it — this page reconnects by itself in a few seconds.' };
  }
  if (body.status === 'restart_needed') {
    const working = (body.working || []).join(', ');
    return { kind: APPLY_KIND.NOTE, text: `Saved. Restart kato to use it — agents are working now${working ? ` (${working})` : ''}.` };
  }
  return body.ok
    ? { kind: APPLY_KIND.OK, text: 'Kato checked it too — it gets through.' }
    : { kind: APPLY_KIND.ERROR, text: `Kato still can't get through: ${body.error || 'no reason given'}` };
}

export const SAVE_TO_APPLY = { kind: APPLY_KIND.NOTE, text: 'Connected — press Save to put it to work in kato.' };
