import { useEffect, useRef } from 'react';
import { toast } from '../stores/toastStore.js';

// Once a first-run setup finishes and kato is running, say what to do next —
// the setup screen just closes, and a new user is left looking at an empty
// window. Shown once, on that change only (not on a reload of a running kato,
// and not after a lost connection comes back).
export const SETUP_FINISHED_NOTE = {
  kind: 'success',
  title: 'Kato is running',
  message: 'Next: create your first task with the button in the task strip. To open pull requests, '
    + 'kato needs your git host login — Settings → Git providers.',
  durationMs: 20000,
};

export function useSetupFinishedNote(status) {
  const wasFirstRun = useRef(false);
  useEffect(() => {
    if (!status) { return; }
    if (status.setup_mode) {
      wasFirstRun.current = status.setup_kind !== 'cannot_connect';
      return;
    }
    if (wasFirstRun.current) {
      wasFirstRun.current = false;
      toast.show(SETUP_FINISHED_NOTE);
    }
  }, [status]);
}
