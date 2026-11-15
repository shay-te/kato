import { useState } from 'react';
import Icon, { BusyIcon } from '../Icon.jsx';

// "Test connection" for a provider settings tab: tests what the form holds —
// before Save — with kato's own check, and says the outcome in full: a green
// line when it answered, the provider's own error, whole, when it did not.
// ``test`` resolves to the API envelope ``{ ok, body: { ok, error }, error }``.
// ``fixesFor(errorText)`` (optional) → [{ key, value, label }]: fields kato can
// fill in from the failure itself; ``onApply(fix)`` fills one into the form.
// ``onConnected()`` (optional) hears a test that got through.
export default function ConnectionTest({ label, test, fixesFor = null, onApply = null, onConnected = null }) {
  const [testing, setTesting] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const run = async () => {
    setTesting(true);
    setOutcome(null);
    let result;
    try {
      result = await test();
    } catch (error) {
      result = { ok: false, error: String(error?.message || error) };
    }
    setTesting(false);
    if (result?.ok && result.body?.ok) {
      setOutcome({ ok: true, text: result.body.message || `Connected — kato reached ${label}.` });
      onConnected?.();
      return;
    }
    setOutcome({
      ok: false,
      text: result?.body?.error || result?.error || `kato could not reach ${label}.`,
    });
  };

  return (
    <div className="connection-test">
      <button type="button" className="settings-drawer-action-secondary connection-test-button" disabled={testing} onClick={run}>
        {testing ? <BusyIcon busy idle="check" /> : <Icon name="check" />}
        <span>{testing ? `Testing ${label}…` : 'Test connection'}</span>
      </button>
      {outcome?.ok && <p className="connection-test-ok" role="status">{outcome.text}</p>}
      {outcome && !outcome.ok && (
        <div className="connection-test-failed" role="alert">
          <p>Could not connect to {label}:</p>
          <pre>{outcome.text}</pre>
          {onApply && (fixesFor?.(outcome.text) || []).map((fix) => (
            <button
              key={fix.key}
              type="button"
              className="settings-drawer-action-primary connection-test-fix"
              onClick={() => {
                onApply(fix);
                setOutcome({ ok: true, text: `Filled in ${fix.value} — press Test connection again, then Save.` });
              }}
            >
              {fix.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
