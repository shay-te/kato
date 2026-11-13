import { useEffect, useRef, useState } from 'react';
import { fetchTaskUnpushedWork } from '../api.js';
import { useDelayedFlag } from '../hooks/useDelayedFlag.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { mayLoseWork, unpushedWorkLines } from '../utils/unpushedWork.js';
import DialogShell from './DialogShell.jsx';

// How long the check may run before "Checking…" is shown. It is local git and
// usually answers in a blink; a dialog painted for those frames is a flash.
export const CHECKING_GRACE_MS = 300;

/**
 * The step BEFORE the delete dialog: does this task hold work that was never
 * pushed?
 *
 * Deleting a task deletes its workspace clones, and with them every commit
 * and edit the remote does not have. The delete dialog says that in general
 * words; this says it about THIS task — which repositories, how many commits,
 * how many files — and only when it is true. Nothing to lose → it renders
 * nothing and hands straight on to the delete dialog (``onContinue``).
 *
 * "Could not check" (a clone git could not read, a failed request) warns too:
 * the operator decides, not a guess. Cancel is focused; Esc cancels. ``onOpenTask``
 * takes the operator to the task to push it first.
 */
export const WARNING_PURPOSE = Object.freeze({
  DELETE: 'delete', HANDOVER: 'handover', HANDOVER_AND_DELETE: 'handover_and_delete',
});

// What is about to happen to the task, in the warning's words. Deleting loses
// unpushed work for good; handing over leaves it here, where the other kato —
// which clones from the remote — never sees it; handing over AND deleting it
// here does both: it never reaches them and it is gone from here too.
const WARNING_WORDS = {
  [WARNING_PURPOSE.DELETE]: {
    before: 'before it can be deleted',
    unknown: 'Deleting the task deletes its workspace, and anything not pushed with it.',
    found: <>Deleting the task deletes its workspace, and this work with it &mdash; there is no backup.</>,
    keep: <>To keep it, open the task and press <strong>Push</strong> first.</>,
    proceed: 'Delete anyway…',
  },
  [WARNING_PURPOSE.HANDOVER]: {
    before: 'before it is handed over',
    unknown: 'Their kato clones from the remote, so anything not pushed stays here.',
    found: <>Their kato clones from the remote, so this work would not reach them.</>,
    keep: <>To hand it over with this work, press <strong>Push</strong> first.</>,
    proceed: 'Hand over anyway',
  },
  [WARNING_PURPOSE.HANDOVER_AND_DELETE]: {
    before: 'before it is handed over and deleted here',
    unknown: 'Their kato clones from the remote, and deleting the task here deletes anything not pushed for good.',
    found: <>Their kato clones from the remote, so this work would not reach them &mdash; and deleting the task here deletes it, with no backup.</>,
    keep: <>To keep it, press <strong>Push</strong> first.</>,
    proceed: 'Hand over and delete anyway',
  },
};

export default function UnpushedWorkWarning({
  session, onContinue, onCancel, onOpenTask, purpose = WARNING_PURPOSE.DELETE,
}) {
  const taskId = String(session?.task_id || '').trim();
  const words = WARNING_WORDS[purpose] || WARNING_WORDS[WARNING_PURPOSE.DELETE];
  // undefined = still checking; null = could not ask at all.
  const [answer, setAnswer] = useState(undefined);
  const cancelRef = useRef(null);
  const onContinueRef = useRef(onContinue);
  onContinueRef.current = onContinue;

  useEscapeKey(onCancel);

  useEffect(() => {
    let live = true;
    fetchTaskUnpushedWork(taskId).then(
      (body) => { if (live) { setAnswer(body && typeof body === 'object' ? body : null); } },
      () => { if (live) { setAnswer(null); } },
    );
    return () => { live = false; };
  }, [taskId]);

  const checking = answer === undefined;
  const warn = !checking && mayLoseWork(answer);
  const showChecking = useDelayedFlag(checking, CHECKING_GRACE_MS);

  useEffect(() => {
    if (!checking && !warn) { onContinueRef.current(answer); }
  }, [checking, warn, answer]);

  useEffect(() => {
    if (warn || showChecking) { cancelRef.current?.focus(); }
  }, [warn, showChecking]);

  if (!warn && !showChecking) { return null; }

  const cancelButton = (
    <button
      id="unpushed-work-cancel"
      type="button"
      className="secondary"
      ref={cancelRef}
      onClick={onCancel}
    >
      Cancel
    </button>
  );

  if (!warn) {
    return (
      <DialogShell
        id="unpushed-work-modal"
        ariaLabelledBy="unpushed-work-title"
        title="Checking for unpushed work…"
        subtitle={taskId}
        subtitleId="unpushed-work-name"
        onClose={onCancel}
        backdropClose
      >
        <p className="forget-task-lead">
          Looking for commits and edits in <strong>{taskId}</strong> that
          were never pushed, {words.before}.
        </p>
        <div className="modal-actions">{cancelButton}</div>
      </DialogShell>
    );
  }

  const lines = unpushedWorkLines(answer);
  return (
    <DialogShell
      id="unpushed-work-modal"
      ariaLabelledBy="unpushed-work-title"
      title="Work not pushed"
      subtitle={taskId}
      subtitleId="unpushed-work-name"
      onClose={onCancel}
      backdropClose
    >
      <p className="forget-task-warning" role="alert">
        {answer === null ? (
          <>
            ⚠ Kato <strong>could not check</strong> whether {taskId} has work
            that was never pushed. {words.unknown}
          </>
        ) : (
          <>
            ⚠ <strong>{taskId} has work that was never pushed.</strong>{' '}
            {words.found}
          </>
        )}
      </p>

      {lines.length > 0 && (
        <ul className="forget-task-effects" id="unpushed-work-list">
          {lines.map((line) => (
            <li key={`${line.unchecked ? 'unchecked' : 'unpushed'}:${line.repository}`}>
              <strong>{line.repository || 'workspace'}</strong>{' '}&mdash; {line.text}
            </li>
          ))}
        </ul>
      )}

      <p className="forget-task-lead">{words.keep}</p>

      <div className="modal-actions">
        {cancelButton}
        {typeof onOpenTask === 'function' && (
          <button
            id="unpushed-work-open"
            type="button"
            className="secondary"
            onClick={() => onOpenTask(taskId)}
          >
            Open task
          </button>
        )}
        <button
          id="unpushed-work-continue"
          type="button"
          className="danger"
          onClick={() => onContinue(answer)}
        >
          {words.proceed}
        </button>
      </div>
    </DialogShell>
  );
}
