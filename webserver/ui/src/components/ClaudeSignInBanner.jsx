import { useCallback, useEffect, useState } from 'react';
import { fetchQuickStartChecks } from '../api.js';
import ClaudeSignIn from './ClaudeSignIn.jsx';

// Quick start lets the operator start kato before signing in to Claude Code
// ("sign in later"). Later is HERE: while Claude Code is installed and says it
// is signed out, kato's main screen says so and offers the same sign-in.
//
// Asked only while Claude Code is the agent that matters (``needed``), once
// when it becomes so, and again when the window comes back while signed out —
// a sign-in done in a terminal clears it. Never polled.
export default function ClaudeSignInBanner({ needed }) {
  const [signedOut, setSignedOut] = useState(false);

  const check = useCallback(async () => {
    try {
      const checks = await fetchQuickStartChecks('claude');
      setSignedOut(checks?.agent?.installed === true && checks.agent.logged_in === false);
    } catch {
      // Could not ask: say nothing (a guess is never "signed out").
    }
  }, []);

  useEffect(() => {
    if (needed) { check(); }
  }, [needed, check]);

  useEffect(() => {
    if (!needed || !signedOut) { return undefined; }
    const askAgain = () => {
      if (document.visibilityState === 'visible') { check(); }
    };
    window.addEventListener('focus', askAgain);
    document.addEventListener('visibilitychange', askAgain);
    return () => {
      window.removeEventListener('focus', askAgain);
      document.removeEventListener('visibilitychange', askAgain);
    };
  }, [needed, signedOut, check]);

  if (!needed || !signedOut) { return null; }
  return (
    <div className="kato-version-banner kato-version-banner--warn claude-sign-in-banner" role="alert">
      <span className="kato-version-banner__icon" aria-hidden="true">!</span>
      <div className="claude-sign-in-banner__body">
        <ClaudeSignIn
          note="Claude Code is not signed in — its chats cannot answer until you sign in."
          onChanged={check}
        />
      </div>
    </div>
  );
}
