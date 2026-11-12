import { fetchShutdownPreview, shutdownKato } from '../api.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { toast } from '../stores/toastStore.js';
import { BusyIcon } from './Icon.jsx';
import { cx } from '../utils/cx.js';

// Shut kato down from the UI — the very path Ctrl+C takes: kato writes down
// which agents are working, stops every agent gracefully, and exits. When it
// starts again, those agents are told to continue where they stopped.
// ``tooltipClassName`` places its hover label (the rail's footer opens them
// upward, from the left edge).
export default function ShutdownButton({ tooltipClassName = '' }) {
  const [busy, run] = useBusyAction(async () => {
    let running = [];
    try {
      running = (await fetchShutdownPreview())?.running || [];
    } catch {
      running = [];  // the confirm still asks; the server records them anyway
    }
    if (!window.confirm(shutdownConfirm(running))) { return; }
    const result = await shutdownKato();
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'Kato did not shut down' });
      return;
    }
    toast.show({
      kind: 'info',
      title: 'Kato is shutting down',
      message: shutdownNote(result.body?.running || running),
      durationMs: 0,
    });
  });
  return (
    <button
      type="button"
      className={cx('header-icon-btn', 'header-shutdown', tooltipClassName)}
      data-tooltip="Shut down kato — stops every agent gracefully. Agents that are working continue when kato starts again."
      aria-label="Shut down kato"
      disabled={busy}
      onClick={() => run()}
    >
      <BusyIcon busy={busy} idle="power" />
    </button>
  );
}

export function shutdownConfirm(running) {
  const agents = running.length
    ? `${running.length} agent${running.length === 1 ? ' is' : 's are'} working (${running.join(', ')}). `
      + 'They are stopped now and continue where they stopped when kato starts again.'
    : 'No agent is working right now.';
  return `Shut down kato? ${agents}`;
}

export function shutdownNote(running) {
  return running.length
    ? `Start kato again and ${running.join(', ')} continue${running.length === 1 ? 's' : ''} where ${running.length === 1 ? 'it' : 'they'} stopped.`
    : 'Start kato again to pick up where you left off.';
}
