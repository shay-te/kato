import { useCallback, useEffect, useState } from 'react';
import { fetchAgentBackends, refreshAgentBackends } from '../api.js';

// Is the agent's CLI on this computer installed and signed in? Asked on the
// setup wizard's agent step: installed is not usable, and a new user who
// never signed in otherwise found out at their first task. The words for a
// missing CLI are its transport's own (install commands included).

function statusOf(entry) {
  if (!entry) { return 'unknown'; }
  if (!entry.ready) { return 'missing'; }
  if (entry.logged_in === false) { return 'signed-out'; }
  return entry.logged_in === true ? 'ready' : 'installed';
}

export default function AgentCliStatus({ backend, label, loginCommand }) {
  const [entry, setEntry] = useState(null);
  const [checking, setChecking] = useState(true);
  const [failed, setFailed] = useState('');

  const load = useCallback(async (again) => {
    setChecking(true);
    setFailed('');
    try {
      const body = await (again ? refreshAgentBackends() : fetchAgentBackends());
      setEntry((body?.backends || []).find((item) => item.id === backend) || null);
    } catch (error) {
      setFailed(String(error?.message || error || 'could not ask'));
    } finally {
      setChecking(false);
    }
  }, [backend]);

  useEffect(() => { load(false); }, [load]);

  const status = statusOf(entry);
  let message;
  if (checking) {
    message = <p>Checking {label} on this computer…</p>;
  } else if (failed || status === 'unknown') {
    message = <p>kato could not check {label} right now{failed ? `: ${failed}` : ''}.</p>;
  } else if (status === 'missing') {
    message = (
      <>
        <p><strong>{label} is not installed on this computer yet.</strong></p>
        <pre>{entry.error}</pre>
      </>
    );
  } else if (status === 'signed-out') {
    message = (
      <p>
        <strong>{label} is installed but not signed in.</strong> In a terminal, run{' '}
        <code>{loginCommand}</code>, then check again.
      </p>
    );
  } else if (status === 'ready') {
    message = <p>✓ {label} is installed and signed in.</p>;
  } else {
    message = <p>✓ {label} is installed. Whether it is signed in shows at your first task.</p>;
  }
  return (
    <div className={`agent-cli-status is-${checking ? 'checking' : status}`} role="status">
      {message}
      {checking || status === 'ready' ? null : (
        <button type="button" className="setup-wizard-btn" onClick={() => load(true)}>Check again</button>
      )}
    </div>
  );
}
