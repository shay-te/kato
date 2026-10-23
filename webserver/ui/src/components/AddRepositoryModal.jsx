import { addTaskRepositories } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { REPOSITORY_TAG_PREFIX } from '../utils/katoTags.js';
import RepositoryPickerModal from './RepositoryPickerModal.jsx';

// "+ Add repository" picker for the Files tab: the shared repository picker
// (every repo in kato's inventory, the ones already on the task left out; tick
// as many as you want), and on confirm the platform-side tag writes + workspace
// clones in one call — the clones run together.
export default function AddRepositoryModal({
  taskId,
  alreadyAttachedIds = new Set(),
  onClose,
  onAdded,
}) {
  async function onConfirm(repos) {
    const result = await addTaskRepositories(taskId, repos.map((repo) => repo.id));
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Could not add repository',
        fallback: 'add failed',
        durationMs: 12000,
      });
      return;
    }
    const body = result.body || {};
    const report = addRepositoryReport(body);
    toast.show({
      kind: report.needsAttention || report.nothingNew ? 'warning' : 'success',
      title: 'Add repository',
      message: report.lines.join('\n'),
      // An action that did not fully complete never expires (durationMs 0): a
      // timer would take the report away before it is read.
      durationMs: report.needsAttention ? 0 : 8000,
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
          Pick one or more repositories from kato's inventory — your picks stay
          selected while you search. Kato will tag the task
          with <code>{REPOSITORY_TAG_PREFIX}&lt;id&gt;</code> (in kato, and on the
          ticket when the issue tracker allows it) and clone them into this task's
          workspace. Repositories already attached to the task are filtered out.
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

const plural = (count, one, many) => (count === 1 ? one : many);

// What one add did, as the lines of a toast. A problem is listed FIRST: it is
// what the operator has to act on, and under a pile of green lines it is the
// one that gets missed. Exported for tests.
export function addRepositoryReport(body) {
  const repos = body.repositories || [];
  const rejected = body.rejected || [];
  const sync = body.sync || {};
  const cloned = sync.added_repositories || [];
  const cloneFailures = sync.failed_repositories || [];
  const one = repos.length === 1;
  const ticketFailed = repos.filter((repo) => repo.ticket_tag?.status === 'failed');
  const ticketAdded = repos.filter((repo) => repo.ticket_tag?.status === 'added');
  const tagged = repos.filter((repo) => repo.tag_added).map((repo) => repo.tag_name);
  const alreadyTagged = repos.filter((repo) => !repo.tag_added).map((repo) => repo.tag_name);
  const lines = [];
  rejected.forEach((entry) => {
    lines.push(`✗ not added — ${entry.repository_id}: ${entry.error}`);
  });
  ticketFailed.forEach((repo) => {
    const why = repo.ticket_tag.error || 'the issue tracker refused it';
    lines.push(one ? `✗ ticket NOT tagged: ${why}` : `✗ ticket NOT tagged (${repo.repository_id}): ${why}`);
  });
  if (tagged.length) {
    lines.push(`✓ tagged task with ${tagged.join(', ')}`);
  }
  if (alreadyTagged.length) {
    lines.push(`• ${plural(alreadyTagged.length, 'tag', 'tags')} ${alreadyTagged.join(', ')} ${
      plural(alreadyTagged.length, 'was', 'were')} already on the task`);
  }
  if (cloned.length) {
    lines.push(`✓ cloned ${cloned.length} repo(s): ${cloned.join(', ')}`);
  }
  if (cloneFailures.length) {
    const errors = cloneFailures.map((entry) => `${entry.repository_id}: ${entry.error}`).join('; ');
    lines.push(`✗ clone failed: ${errors}`);
  }
  if (!cloned.length && !cloneFailures.length) {
    lines.push(`• ${plural(repos.length, 'repository was', 'repositories were')} already cloned`);
  }
  if (repos.length && ticketAdded.length === repos.length) {
    lines.push('✓ tagged the ticket too');
  } else if (ticketAdded.length) {
    lines.push(`✓ tagged the ticket with ${ticketAdded.map((repo) => repo.repository_id).join(', ')}`);
  }
  [...new Set(
    repos.filter((repo) => repo.ticket_tag?.status === 'unsupported').map((repo) => repo.ticket_tag.error),
  )].forEach((why) => lines.push(`• ticket not tagged: ${why}`));
  return {
    lines,
    needsAttention: Boolean(ticketFailed.length || cloneFailures.length || rejected.length),
    nothingNew: !cloned.length && !tagged.length,
  };
}
