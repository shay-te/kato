import { useEffect, useState } from 'react';
import {
  cancelClaudeSignIn,
  fetchClaudeSignIn,
  sendClaudeSignInCode,
  startClaudeSignIn,
} from '../api.js';

const CHECK_EVERY_MS = 2500;

// Claude Code's own sign-in, run by kato: the browser opens to sign in; if it
// does not (inside WSL it usually cannot), the link is here, and a code the
// sign-in page shows is pasted here and handed to the CLI.
export default function ClaudeSignIn({ onChanged, note = '' }) {
  const [signIn, setSignIn] = useState(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const waiting = signIn?.state === 'waiting';

  useEffect(() => {
    if (!waiting) { return undefined; }
    const timer = setInterval(async () => {
      try {
        const next = await fetchClaudeSignIn();
        setSignIn(next);
        if (next.state !== 'waiting') { onChanged(); }
      } catch {
        // asked again next tick
      }
    }, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [waiting, onChanged]);

  const begin = async () => {
    setError('');
    const result = await startClaudeSignIn();
    if (!result.ok) { setError(result.body?.error || result.error || 'could not start the sign-in'); return; }
    setSignIn(result.body);
  };
  const send = async () => {
    setError('');
    const result = await sendClaudeSignInCode(code.trim());
    if (!result.ok) { setError(result.body?.error || result.error || 'the code was not taken'); return; }
    setCode('');
    setSignIn(result.body);
  };

  if (!waiting) {
    return (
      <>
        {note && <p className="quick-start-note">{note}</p>}
        {signIn?.state === 'failed' && <p className="setup-wizard-error" role="alert">{signIn.message}</p>}
        {error && <p className="setup-wizard-error" role="alert">{error}</p>}
        <div className="quick-start-actions">
          <button type="button" className="setup-wizard-btn setup-wizard-btn--primary" onClick={begin}>
            Sign in to Claude
          </button>
        </div>
      </>
    );
  }
  return (
    <div className="quick-start-sign-in">
      <p className="quick-start-note" role="status">
        A browser window opened to sign in.
        {signIn.link && (
          <>
            {' '}Didn’t open?{' '}
            <a href={signIn.link} target="_blank" rel="noreferrer">Open the sign-in page</a>.
          </>
        )}
      </p>
      {signIn.asks_for_code && (
        <div className="quick-start-code">
          <input
            type="text"
            className="quick-start-input"
            aria-label="Code from the sign-in page"
            placeholder="If the page shows a code, paste it here"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <button type="button" className="setup-wizard-btn" disabled={!code.trim()} onClick={send}>
            Send code
          </button>
        </div>
      )}
      {error && <p className="setup-wizard-error" role="alert">{error}</p>}
      <button type="button" className="quick-start-link" onClick={async () => setSignIn((await cancelClaudeSignIn()).body)}>
        Cancel
      </button>
    </div>
  );
}
