import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchAgentUpgradeStatus, upgradeAgentCli } from '../api.js';
import { refreshAgentVersion } from './useAgentVersion.js';
import { refreshCatalogs } from './useCatalogRefresh.js';

// Drives the in-app agent-CLI upgrade: starts it, then polls the server-side
// job for the progress bar and the live command output.
//
// The job runs on the HOST, not in this request — so closing the modal or
// reloading the page never orphans an install that's still modifying the
// machine. On mount we re-attach to whatever is already in flight, which is
// what makes the reload case work.
//
// ``progress`` is the server snapshot: { state: idle|running|done|error,
// percent, step, command, lines, ok, message, version_before, version_after }.

const POLL_MS = 700;

// Everything that must be re-read once the binary on the host has changed.
// The model picker is part of it: a new CLI can resolve its aliases to newer
// models, so re-probing only the version would leave the picker showing the
// OLD CLI's labels until the operator hit the header Refresh.
function reprobeAfterUpgrade() {
  refreshAgentVersion();
  refreshCatalogs();
}

export function useAgentUpgrade(backend = '') {
  const [progress, setProgress] = useState(null);
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // One poll, self-rescheduling while the job is still running. A failed poll
  // is not terminal — the job keeps going on the host, so we simply try again
  // rather than reporting a false failure.
  const poll = useCallback(async () => {
    let snapshot = null;
    try {
      snapshot = await fetchAgentUpgradeStatus();
    } catch {
      snapshot = null;
    }
    if (!aliveRef.current) { return; }
    if (snapshot) {
      setProgress(snapshot);
      if (snapshot.state !== 'running') {
        stopPolling();
        // The binary just changed — re-probe so the banner, the ultracode
        // toggle AND the model picker reflect the new CLI with no reload.
        reprobeAfterUpgrade();
        return;
      }
    }
    timerRef.current = setTimeout(poll, POLL_MS);
  }, [stopPolling]);

  // Re-attach to an upgrade already running when this mounts (page reload,
  // or the operator reopening the modal after dismissing it).
  useEffect(() => {
    aliveRef.current = true;
    let cancelled = false;
    fetchAgentUpgradeStatus()
      .then((snapshot) => {
        if (cancelled || !snapshot) { return; }
        if (snapshot.state === 'running') {
          setProgress(snapshot);
          timerRef.current = setTimeout(poll, POLL_MS);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      aliveRef.current = false;
      stopPolling();
    };
  }, [poll, stopPolling]);

  const start = useCallback(async () => {
    // Optimistic "running" so the bar appears on click instead of after the
    // first round trip.
    setProgress({ state: 'running', percent: 0, step: 'Starting…', lines: [] });
    let body = null;
    try {
      // Names the CLI to upgrade: without it the server resolves the
      // CONFIGURED backend, so the Codex tab's button installs a new Claude.
      const result = await upgradeAgentCli(backend);
      body = (result && result.body) || null;
    } catch {
      body = null;
    }
    if (!aliveRef.current) { return; }
    if (body) { setProgress(body); }
    if (body && body.state !== 'running') {
      reprobeAfterUpgrade();
      return;
    }
    stopPolling();
    timerRef.current = setTimeout(poll, POLL_MS);
  }, [poll, stopPolling]);

  // Clear the finished result so the modal can close. Never clears a RUNNING
  // job — that would hide an install still touching the host.
  const dismiss = useCallback(() => {
    setProgress((current) => (current && current.state === 'running' ? current : null));
  }, []);

  return { progress, start, dismiss };
}
