import { updateTaskSource } from '../api.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { gitActionKey } from '../stores/gitActionStore.js';
import { toastResult } from '../stores/toastStore.js';
import { BusyIcon } from './Icon.jsx';
import { formatUpdateSourceResult } from './sessionHeaderFormatters.js';

// Update source for ONE repository, from its header in the Changes tab: push
// it, then switch only its source checkout to the task's branch. The header's
// Update source moves every repository, and an operator verifying "a small
// client change" did not want the backend's checkout to move while Docker was
// building from it.
//
// The task's one git slot (``gitActionKey(task, 'update-source')``), so it and
// the header's button never run at once; the same sticky per-repo toast.
export default function RepoUpdateSourceButton({ taskId, repoId, heading }) {
  const [busy, run] = useBusyAction(() => updateTaskSource(taskId, repoId), {
    scope: gitActionKey(taskId, 'update-source'),
    onDone: (result) => toastResult(formatUpdateSourceResult(result), { defaultMs: 0 }),
  });
  return (
    <button
      type="button"
      className="files-tab-repo-update-source-btn tooltip-end"
      onClick={(event) => {
        event.stopPropagation();  // the header toggles the repo open/closed
        run();
      }}
      disabled={busy}
      data-tooltip={`Update source — only ${heading}`}
      aria-label={busy ? `Updating the ${heading} source…` : `Update the ${heading} source only`}
    >
      <BusyIcon busy={busy} idle="refresh" />
    </button>
  );
}
