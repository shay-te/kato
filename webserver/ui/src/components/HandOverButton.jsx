import { useEffect, useMemo, useState } from 'react';
import { fetchHandoverAvailability, fetchHandoverUsers, handOverTask } from '../api.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { usePickerData } from '../hooks/usePickerData.js';
import { toastResult } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { BusyIcon } from './Icon.jsx';
import SearchPickerModal from './SearchPickerModal.jsx';
import UnpushedWorkWarning, { WARNING_PURPOSE } from './UnpushedWorkWarning.jsx';
import { formatHandoverResult } from './sessionHeaderFormatters.js';

export const HANDOVER_TOOLTIP = 'Hand over — give this task to a teammate: kato assigns the ticket to them, '
  + 'tags it kato:wait-editing, moves it to Open and stops the agent here, and THEIR kato picks it up. '
  + "Anyone the ticket can be assigned to is listed — kato cannot tell who runs a kato.";

// The header's "Hand over" (change the task's owner). Hidden for a task no one
// else's kato can see (one created in kato, a tracker that cannot) — asked of
// the server once per task, without a tracker call. The picker asks the tracker
// who the ticket can go to; work never pushed is warned about first (their kato
// clones from the remote); the server does the rest and says how each step went.
// Each task's answer, kept for this page: switching back to a task shows its
// button at once instead of popping it in after a request. A task not asked
// about yet starts from the last answer seen — it is mostly the tracker's, the
// same for every task — so the header does not jump on every switch.
const availabilityByTask = new Map();
let lastAnswer = false;

export function forgetHandoverAvailability() {
  availabilityByTask.clear();
  lastAnswer = false;
}

function remembered(taskId) {
  return availabilityByTask.has(taskId) ? availabilityByTask.get(taskId) : lastAnswer;
}

export default function HandOverButton({ taskId }) {
  const [available, setAvailable] = useState(() => remembered(taskId));
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState(null);
  const [busy, handOver] = useBusyAction(
    (user) => handOverTask(taskId, user.handle, user.name),
    { onDone: (result) => toastResult(formatHandoverResult(result)) },
  );

  useEffect(() => {
    let live = true;
    setAvailable(remembered(taskId));
    const settle = (answer) => {
      availabilityByTask.set(taskId, answer);
      lastAnswer = answer;
      if (live) { setAvailable(answer); }
    };
    fetchHandoverAvailability(taskId).then(
      (body) => settle(!!(body && body.available)),
      () => settle(false),
    );
    return () => { live = false; };
  }, [taskId]);

  if (!available) { return null; }

  return (
    <>
      <button
        id="session-handover"
        type="button"
        className="session-action"
        data-tooltip={HANDOVER_TOOLTIP}
        onClick={() => setPicking(true)}
        disabled={busy}
        aria-label={busy ? 'Handing over…' : 'Hand over'}
      >
        <BusyIcon busy={busy} idle="user-handover" />
      </button>
      {picking && (
        <HandOverPicker
          taskId={taskId}
          onClose={() => setPicking(false)}
          onPick={(user) => { setPicking(false); setChosen(user); }}
        />
      )}
      {chosen && (
        <UnpushedWorkWarning
          session={{ task_id: taskId }}
          purpose={WARNING_PURPOSE.HANDOVER}
          onCancel={() => setChosen(null)}
          onContinue={() => { const user = chosen; setChosen(null); handOver(user); }}
        />
      )}
    </>
  );
}

function HandOverPicker({ taskId, onClose, onPick }) {
  const [query, setQuery] = useState('');
  const { data: users, loading, error } = usePickerData(async () => {
    const result = await fetchHandoverUsers(taskId);
    if (!result.ok) { throw new Error(apiErrorMessage(result, 'could not list who the ticket can go to')); }
    return Array.isArray(result.body?.users) ? result.body.users : [];
  }, [taskId], []);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) { return users; }
    return users.filter((user) => `${user.name}\n${user.handle}`.toLowerCase().includes(needle));
  }, [users, query]);

  return (
    <SearchPickerModal
      ariaLabel="Hand over this task"
      title={`Hand over ${taskId}`}
      onClose={onClose}
      helpText="Their kato picks it up: the ticket is assigned to them, tagged kato:wait-editing and moved to Open."
      searchPlaceholder="Search by name or login…"
      query={query}
      onQueryChange={setQuery}
      items={shown}
      loading={loading}
      error={error}
      loadingText="Asking the tracker who it can go to…"
      emptyText={users.length === 0 ? 'The tracker lists no one this ticket can go to.' : 'No one matches.'}
      getItemId={(user) => user.handle}
      confirmLabel="Hand over"
      busyLabel="Handing over…"
      onConfirm={onPick}
      renderRow={(user) => (
        <div className="adopt-session-row-top">
          <span className="adopt-session-cwd">{user.name}</span>
          {user.name !== user.handle && <span className="adopt-session-meta">{user.handle}</span>}
        </div>
      )}
    />
  );
}
