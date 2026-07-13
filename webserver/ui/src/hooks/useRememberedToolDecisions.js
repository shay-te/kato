import { useCallback, useState } from 'react';
import { fetchToolDecisions } from '../api.js';
import { usePolling } from './usePolling.js';

const POLL_INTERVAL_MS = 15_000;

// Read-only cache of the BACKEND's remembered tool-permission decisions
// (kato_core_lib/helpers/tool_decision_store.py is the sole source of
// truth — the browser holds no decision of its own). The actual
// approve/deny/auto-resolve logic lives entirely server-side; this
// cache only feeds a UI de-dup hint (see useNotificationRouting: the
// status-feed log line fires unconditionally at capture time, before
// the backend's own auto-resolve runs, so the notification path needs
// its own hint to avoid pinging the operator for a decision already
// made). Re-fetched on an interval so it stays fresh across tabs.
export function useRememberedToolDecisions() {
  const [decisions, setDecisions] = useState([]);

  const refresh = useCallback(async () => {
    const result = await fetchToolDecisions();
    if (result.ok && Array.isArray(result.body?.decisions)) {
      setDecisions(result.body.decisions);
    }
  }, []);

  usePolling(refresh, POLL_INTERVAL_MS, [], { enabled: true });

  const recall = useCallback((toolName, commandSignature = '') => {
    const match = decisions.find(
      (entry) => entry.tool_name === toolName
        && entry.command_signature === commandSignature,
    );
    if (!match) { return null; }
    return match.allow ? 'allow' : 'deny';
  }, [decisions]);

  return { recall, refresh, decisions };
}
