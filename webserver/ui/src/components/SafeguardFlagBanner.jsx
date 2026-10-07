import { useEffect, useMemo, useState } from 'react';
import { fetchSafeguardFallback, retryOnFallbackModel } from '../api.js';
import { findSafeguardFlag } from '../utils/safeguardError.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { toastResult } from '../stores/toastStore.js';
import Icon, { BusyIcon } from './Icon.jsx';

// Shown above the composer when the LATEST turn was refused by the API's
// safeguards ("safeguards flagged this session … Details: [cyber]"). The same
// prompt fails the same way on this model, so the banner offers the one
// recovery that works: retry on the pinned fallback model (a lower version) —
// kato respawns the chat on it and resends the flagged message. Operator-
// triggered, like VS Code's own "fall back" affordance; never automatic.
//
// Renders nothing unless there is a live flag AND a fallback model is
// configured (``KATO_CLAUDE_FALLBACK_MODEL``).
export default function SafeguardFlagBanner({ taskId, entries }) {
  const flag = useMemo(() => findSafeguardFlag(entries), [entries]);
  const [fallback, setFallback] = useState({ model: '', label: '' });

  useEffect(() => {
    let cancelled = false;
    Promise.resolve(fetchSafeguardFallback()).then((result) => {
      if (!cancelled) {
        setFallback({ model: String(result?.model || ''), label: String(result?.label || '') });
      }
    });
    return () => { cancelled = true; };
  }, []);

  const [retrying, retry] = useBusyAction(() => retryOnFallbackModel(taskId), {
    onDone: (result) => {
      toastResult(result?.ok
        ? { kind: 'success', title: `Retrying on ${fallback.label}`, message: 'Respawned on the fallback model and resent your message.', taskId }
        : { kind: 'error', title: 'Couldn’t retry', message: result?.body?.error || result?.error || 'kato did not accept the retry', taskId });
    },
  });

  const show = !!flag && !!fallback.model;
  if (!show) { return null; }

  const detail = flag.details ? ` (${flag.details})` : '';
  const headline = `Anthropic’s safeguards flagged this turn${detail}.`;
  const explain = `The same request keeps failing on this model. Retry it on ${fallback.label}, a lower version.`;
  const requestLine = flag.requestId
    ? <span className="safeguard-flag-request">Request {flag.requestId}</span>
    : null;

  return (
    <div className="safeguard-flag-banner" role="alert">
      <Icon name="warning" />
      <div className="safeguard-flag-body">
        <p className="safeguard-flag-headline">{headline}</p>
        <p className="safeguard-flag-explain">{explain}</p>
        {requestLine}
      </div>
      <button
        type="button"
        className="safeguard-flag-retry"
        onClick={retry}
        disabled={retrying}
      >
        <BusyIcon busy={retrying} idle="refresh" />
        <span>{`Retry on ${fallback.label}`}</span>
      </button>
    </div>
  );
}
