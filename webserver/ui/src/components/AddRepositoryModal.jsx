import { addTaskRepository } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { REPOSITORY_TAG_PREFIX } from '../utils/katoTags.js';
import RepositoryPickerModal from './RepositoryPickerModal.jsx';

// "+ Add repository" picker for the Files tab: the shared repository picker
// (every repo in kato's inventory, the ones already on the task left out), and
// on confirm the platform-side tag write + workspace clone in one call.
export default function AddRepositoryModal({
  taskId,
  alreadyAttachedIds = new Set(),
  onClose,
  onAdded,
}) {
  async function onConfirm(repo) {
    const result = await addTaskRepository(taskId, repo.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Could not add repository',
        fallback: 'add failed',
        durationMs: 12000,
      });
      return;
    }
    const body = result.body || {};
    const tagAdded = body.tag_added;
    const sync = body.sync || {};
    const cloned = (sync.added_repositories || []).length;
    const ticket = body.ticket_tag || {};
    const cloneFailed = cloned === 0 && (sync.failed_repositories || []).length > 0;
    const ticketFailed = ticket.status === 'failed';
    const lines = [];
    // A problem is listed FIRST: it is what the operator has to act on, and
    // under a pile of green lines it is the one that gets missed.
    if (ticketFailed) {
      lines.push(`✗ ticket NOT tagged: ${ticket.error || 'the issue tracker refused it'}`);
    }
    if (tagAdded) {
      lines.push(`✓ tagged task with ${body.tag_name}`);
    } else {
      lines.push(`• tag ${body.tag_name} was already on the task`);
    }
    if (cloned > 0) {
      lines.push(`✓ cloned ${cloned} repo(s): ${(sync.added_repositories || []).join(', ')}`);
    } else if (cloneFailed) {
      const errs = sync.failed_repositories
        .map((entry) => `${entry.repository_id}: ${entry.error}`).join('; ');
      lines.push(`✗ clone failed: ${errs}`);
    } else {
      lines.push('• repository was already cloned');
    }
    if (ticket.status === 'added') {
      lines.push('✓ tagged the ticket too');
    } else if (ticket.status === 'unsupported') {
      lines.push(`• ticket not tagged: ${ticket.error}`);
    }
    // An action that did not fully complete never expires (durationMs 0): a
    // timer would take the report away before it is read.
    const needsAttention = ticketFailed || cloneFailed;
    toast.show({
      kind: needsAttention || (cloned === 0 && !tagAdded) ? 'warning' : 'success',
      title: 'Add repository',
      message: lines.join('\n'),
      durationMs: needsAttention ? 0 : 8000,
    });
    if (typeof onAdded === 'function') { onAdded(body); }
    onClose();
  }

  return (
    <RepositoryPickerModal
      ariaLabel="Add repository to task"
      title={<>Add repository to {taskId}</>}
      onClose={onClose}
      helpText={(
        <>
          Pick a repository from kato's inventory. Kato will tag the
          task with <code>{REPOSITORY_TAG_PREFIX}&lt;id&gt;</code> (in kato, and
          on the ticket when the issue tracker allows it) and clone the
          repo into this task's workspace. Repositories already
          attached to the task are filtered out.
        </>
      )}
      alreadyAttachedIds={alreadyAttachedIds}
      allAttachedText="Every repository in the inventory is already attached to this task."
      confirmLabel="Add to task"
      busyLabel="Adding…"
      onPick={onConfirm}
    />
  );
}
