import { useState } from 'react';
import { renameTaskBranch } from '../api.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { toastResult } from '../stores/toastStore.js';
import DialogShell from './DialogShell.jsx';
import Icon from './Icon.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';
import { formatRenameBranchResult } from './sessionHeaderFormatters.js';

// The server refuses a rename once the branch has reached a remote, and says
// why. That takes a call to each remote, so the button cannot know beforehand.
export default function RenameBranchButton({ taskId, branch, disabled, onRenamed }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        id="session-rename-branch"
        type="button"
        className="session-action"
        data-tooltip="Rename this task's branch. Only while it has never been pushed."
        onClick={() => setOpen(true)}
        disabled={disabled}
        aria-label="Rename branch"
      >
        <Icon name="edit" />
      </button>
      {open && (
        <RenameBranchDialog
          taskId={taskId}
          branch={branch}
          onClose={() => setOpen(false)}
          onRenamed={onRenamed}
        />
      )}
    </>
  );
}

function RenameBranchDialog({ taskId, branch, onClose, onRenamed }) {
  const [name, setName] = useState(branch);
  const wanted = name.trim();
  const [busy, submit] = useBusyAction(
    () => renameTaskBranch(taskId, wanted),
    {
      onDone: (result) => {
        toastResult(formatRenameBranchResult(result));
        if (result?.ok) {
          onRenamed?.();
          onClose();
        }
      },
    },
  );
  useEscapeKey(onClose, !busy);

  return (
    <DialogShell
      id="rename-branch-modal"
      ariaLabelledBy="rename-branch-title"
      title="Rename branch"
      subtitle={taskId}
      subtitleId="rename-branch-task"
      onClose={onClose}
      backdropClose
    >
      <label className="new-task-field rename-branch-field">
        <span className="new-task-field-label">Branch name</span>
        <input
          autoFocus
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && wanted && wanted !== branch) { submit(); }
          }}
        />
      </label>
      <ModalFooterActions
        onCancel={onClose}
        onConfirm={submit}
        busy={busy}
        canConfirm={!!wanted && wanted !== branch}
        confirmLabel="Rename"
        busyLabel="Renaming…"
      />
    </DialogShell>
  );
}
