import { useEffect, useMemo, useState } from 'react';
import { fetchHandoverAvailability, fetchHandoverUsers, handOverTask } from '../api.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { usePickerData } from '../hooks/usePickerData.js';
import { toastResult } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { avatarColor, avatarInitials } from '../utils/avatar.js';
import { BusyIcon } from './Icon.jsx';
import SearchPickerModal from './SearchPickerModal.jsx';
import UnpushedWorkWarning, { WARNING_PURPOSE } from './UnpushedWorkWarning.jsx';
import { formatHandoverResult, handoverLanded } from './sessionHeaderFormatters.js';

export const HANDOVER_TOOLTIP = 'Hand over — give this task to a teammate: kato assigns the ticket to them, '
  + 'tags it kato:wait-editing (unless you let their kato start on it by itself), moves it to Open and stops '
  + 'the agent here, and THEIR kato picks it up. '
  + "Anyone the ticket can be assigned to is listed — kato cannot tell who runs a kato.";

// "Their kato waits for them": on every time the picker opens — off, their kato
// works on the ticket with no one watching. The two read about as long, so
// flipping the switch does not move the dialog.
export const WAIT_FOR_THEM_HINT = 'Their kato opens it as a chat and waits until they write in it (kato:wait-editing).';
export const START_BY_ITSELF_HINT = 'Their kato starts on it by itself, unwatched. Any kato:wait-editing tag is taken off.';

// Why "also delete it from my tasks" cannot be ticked yet / at all, when the
// server's own answer is not in hand. Missing = not offered: never a delete on
// a guess (an older kato says nothing about it).
export const CHECKING_REMOVE_HERE = 'kato is checking whether it can be deleted here';
export const UNCHECKED_REMOVE_HERE = 'kato could not check whether deleting it here is safe';

// The header's "Hand over" (change the task's owner). Hidden for a task no one
// else's kato can see (one created in kato, a tracker that cannot) — asked of
// the server once per task, without a tracker call. The picker asks the tracker
// who the ticket can go to; work never pushed is warned about first (their kato
// clones from the remote); the server does the rest and says how each step went.
//
// The picker's "Their kato waits for them" (ticked each time it opens) decides
// whether the ticket is tagged kato:wait-editing or left for their kato to
// start on by itself.
//
// ``onRemoveHere(taskId)`` is the tab's own delete (App's ``doForgetTask``):
// given, the picker offers "also delete it from my tasks", carried out only once
// the server says the ticket really reached them (``landed``). Not offered for a
// task a plan this kato drives was split from — asked again each time the
// picker opens, since a plan can be split while the header stays up.
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

export default function HandOverButton({ taskId, onRemoveHere = null }) {
  const [available, setAvailable] = useState(() => remembered(taskId));
  const [picking, setPicking] = useState(false);
  // {user, waitForThem, removeHere}: who it goes to, whether their kato waits
  // for them, and whether to delete it here after.
  const [chosen, setChosen] = useState(null);
  const offerRemoveHere = typeof onRemoveHere === 'function';
  const [busy, handOver] = useBusyAction(
    async ({ user, waitForThem, removeHere }) => ({
      removeHere, result: await handOverTask(taskId, user.handle, user.name, { waitForThem }),
    }),
    {
      onDone: ({ removeHere, result }) => {
        toastResult(formatHandoverResult(result, { removeHere }));
        if (removeHere && handoverLanded(result)) { onRemoveHere(taskId); }
      },
    },
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
          offerRemoveHere={offerRemoveHere}
          onClose={() => setPicking(false)}
          onPick={(pick) => { setPicking(false); setChosen(pick); }}
        />
      )}
      {chosen && (
        <UnpushedWorkWarning
          session={{ task_id: taskId }}
          purpose={chosen.removeHere ? WARNING_PURPOSE.HANDOVER_AND_DELETE : WARNING_PURPOSE.HANDOVER}
          onCancel={() => setChosen(null)}
          onContinue={() => { const pick = chosen; setChosen(null); handOver(pick); }}
        />
      )}
    </>
  );
}

function HandOverPicker({ taskId, offerRemoveHere, onClose, onPick }) {
  const [query, setQuery] = useState('');
  const [waitForThem, setWaitForThem] = useState(true);
  const [removeHere, setRemoveHere] = useState(false);
  const removeHereReason = useRemoveHereReason(taskId, offerRemoveHere);
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
      helpText="Their kato picks it up: the ticket is assigned to them and moved to Open."
      searchPlaceholder="Search by name or login…"
      query={query}
      onQueryChange={setQuery}
      items={shown}
      loading={loading}
      error={error}
      loadingText="Asking the tracker who it can go to…"
      emptyText={users.length === 0 ? 'The tracker lists no one this ticket can go to.' : 'No one matches.'}
      getItemId={(user) => user.handle}
      confirmLabel={removeHere && !removeHereReason ? 'Hand over and delete here' : 'Hand over'}
      busyLabel="Handing over…"
      onConfirm={(user) => onPick({ user, waitForThem, removeHere: removeHere && !removeHereReason })}
      extra={(
        <div className="handover-options">
          <HandoverOption
            id="handover-wait-for-them"
            label="Their kato waits for them"
            hint={waitForThem ? WAIT_FOR_THEM_HINT : START_BY_ITSELF_HINT}
            checked={waitForThem}
            onChange={setWaitForThem}
          />
          {offerRemoveHere && (
            <HandoverOption
              id="handover-remove-here"
              label="Also delete it from my tasks"
              hint={removeHereReason
                ? `Not offered — ${removeHereReason}.`
                : `Once the ticket is theirs, ${taskId} is deleted here like the tab's ✕: its workspace, chat and `
                  + 'containers. Kept if the hand-over does not get through.'}
              checked={removeHere && !removeHereReason}
              disabled={!!removeHereReason}
              onChange={setRemoveHere}
            />
          )}
        </div>
      )}
      renderRow={(user) => <Person user={user} />}
    />
  );
}

// The server's word on whether deleting the task here is safe ('' = it is),
// asked when the picker opens. Until it answers, or if it cannot, the option
// stays off with the reason shown.
function useRemoveHereReason(taskId, offered) {
  const [reason, setReason] = useState(CHECKING_REMOVE_HERE);
  useEffect(() => {
    if (!offered) { return undefined; }
    let live = true;
    fetchHandoverAvailability(taskId).then(
      (body) => {
        if (!live) { return; }
        const answer = body && body.remove_here_reason;
        setReason(typeof answer === 'string' ? answer : UNCHECKED_REMOVE_HERE);
      },
      () => { if (live) { setReason(UNCHECKED_REMOVE_HERE); } },
    );
    return () => { live = false; };
  }, [taskId, offered]);
  return reason;
}

// A person the ticket can go to: a monogram (the diff comments' own,
// utils/avatar.js — one colour per name), the name, and the tracker's login
// when it says something the name does not.
function Person({ user }) {
  return (
    <span className="handover-person">
      <span
        className="handover-avatar"
        style={{ backgroundColor: avatarColor(user.name || user.handle) }}
        aria-hidden="true"
      >
        {avatarInitials(user.name || user.handle)}
      </span>
      <span className="handover-person-name">{user.name}</span>
      {user.name !== user.handle && <span className="handover-person-handle">{user.handle}</span>}
    </span>
  );
}

// One choice that goes with the hand-over: what it is, what it does, and a
// switch (a real checkbox, drawn as a switch).
function HandoverOption({ id, label, hint, checked, disabled = false, onChange }) {
  return (
    <label className={disabled ? 'handover-option is-disabled' : 'handover-option'} htmlFor={id}>
      <span className="handover-option-text">
        <span className="handover-option-label">{label}</span>
        <span className="handover-option-hint">{hint}</span>
      </span>
      <input
        id={id}
        type="checkbox"
        className="kato-switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}
