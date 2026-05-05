import { useEffect, useReducer, useRef, useState } from 'react';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { safeParseJSON } from '../utils/sse.js';

export const SESSION_LIFECYCLE = {
  CONNECTING: 'connecting',
  IDLE: 'idle',           // record exists but no live subprocess
  STREAMING: 'streaming', // events flowing
  CLOSED: 'closed',
  MISSING: 'missing',     // server has no record for this task
};

const ACTION_HYDRATE = 'hydrate';
const ACTION_INCOMING_EVENT = 'incoming_event';
const ACTION_INCOMING_HISTORY = 'incoming_history';
const ACTION_LIFECYCLE = 'lifecycle';
const ACTION_LOCAL_EVENT = 'local_event';
const ACTION_DISMISS_PERMISSION = 'dismiss_permission';
const ACTION_MARK_TURN_BUSY = 'mark_turn_busy';

// Per-task chat state lives in this module-level Map so it survives the
// SessionDetail unmount/remount cycle that React triggers on tab switch
// (see App.jsx `<SessionDetail key={activeSessionKey} />`). Without this
// cache, switching tabs blows away every LOCAL bubble ("✓ delivered",
// "✗ session stopped", in-flight typed messages) plus any kato-injected
// synthetic event that lives only in the server's `recent_events`
// buffer — the operator sees the chat "shrink" by however many of
// those entries had accumulated. Hydrating from the cache restores the
// previously-seen entries; dedupe on incoming SSE replay (history +
// backlog) prevents the server from doubling them.
const TASK_STREAM_CACHE = new Map();

let _localEventCounter = 0;

function emptyTaskState() {
  return {
    events: [],
    eventKeys: new Set(),
    lifecycle: SESSION_LIFECYCLE.CONNECTING,
    turnInFlight: false,
    pendingPermission: null,
    lastEventAt: 0,
  };
}

function readCachedState(taskId) {
  if (!taskId) { return emptyTaskState(); }
  let entry = TASK_STREAM_CACHE.get(taskId);
  if (!entry) {
    entry = emptyTaskState();
    TASK_STREAM_CACHE.set(taskId, entry);
  }
  return entry;
}

function writeCachedState(taskId, state) {
  if (!taskId) { return; }
  TASK_STREAM_CACHE.set(taskId, state);
}

function entryDedupeKey(entry) {
  // LOCAL entries get a synthetic monotonic id at creation; we can
  // never confuse a local bubble with a server replay, so the id alone
  // is enough.
  if (entry.source === ENTRY_SOURCE.LOCAL) {
    return `local:${entry.localId}`;
  }
  // SERVER entries: prefer the per-event ``received_at_epoch`` the
  // server stamps on each ``SessionEvent``. It's a high-resolution
  // timestamp captured when kato received the event from Claude's
  // stdout, and it's preserved across replays — so a backlog re-emit
  // of the same event reuses the same key. JSON.stringify(raw) is a
  // BAD fallback here: two distinct live events with identical
  // payload (e.g., a respawned Claude emitting another
  // ``system { subtype: init }`` for the same session id) would
  // collide and the second would be silently dropped, freezing the UI
  // until something with different content arrives. The epoch is
  // unique-per-event by construction, so it can't collide.
  if (entry.source === ENTRY_SOURCE.SERVER) {
    const epoch = Number(entry.receivedAtEpoch || 0);
    if (epoch > 0) {
      return `server:${epoch}`;
    }
    // No epoch (older payload shape) — fall back to content. Worst
    // case is over-dedupe of identical-content events; better than
    // re-rendering them as duplicate bubbles.
    try {
      return `server:${JSON.stringify(entry.raw)}`;
    } catch (_) {
      return `server:unserialisable:${Math.random()}`;
    }
  }
  // HISTORY entries always have ``received_at_epoch === 0`` (the
  // server stamps zero on disk-replayed events to mark them as
  // archival). Use raw content for identity — replays of the same
  // JSONL produce identical raw dicts, so this is stable.
  try {
    return `history:${JSON.stringify(entry.raw)}`;
  } catch (_) {
    return `history:unserialisable:${Math.random()}`;
  }
}

function appendEntryIfNew(state, entry) {
  const key = entryDedupeKey(entry);
  if (state.eventKeys.has(key)) {
    return { state, appended: false };
  }
  const eventKeys = new Set(state.eventKeys);
  eventKeys.add(key);
  return {
    state: {
      ...state,
      events: [...state.events, entry],
      eventKeys,
    },
    appended: true,
  };
}

function reducer(state, action) {
  switch (action.type) {
    case ACTION_HYDRATE:
      return action.value;
    case ACTION_INCOMING_EVENT:
      return reduceIncomingEvent(state, action.event, action.receivedAtEpoch);
    case ACTION_INCOMING_HISTORY:
      return reduceIncomingHistory(state, action.event);
    case ACTION_LOCAL_EVENT: {
      _localEventCounter += 1;
      const enriched = { ...action.event, localId: _localEventCounter };
      return appendEntryIfNew(state, enriched).state;
    }
    case ACTION_LIFECYCLE:
      // CLOSED / IDLE / MISSING all mean "nothing live is waiting for input"
      // — drop any stale permission so the modal doesn't pop on a finished tab.
      if (action.value === SESSION_LIFECYCLE.CLOSED
          || action.value === SESSION_LIFECYCLE.IDLE
          || action.value === SESSION_LIFECYCLE.MISSING) {
        return { ...state, lifecycle: action.value, pendingPermission: null };
      }
      return { ...state, lifecycle: action.value };
    case ACTION_DISMISS_PERMISSION:
      return { ...state, pendingPermission: null };
    case ACTION_MARK_TURN_BUSY:
      return { ...state, turnInFlight: action.value };
    default:
      return state;
  }
}

function reduceIncomingEvent(state, raw, receivedAtEpoch) {
  const entry = {
    source: ENTRY_SOURCE.SERVER,
    raw,
    receivedAtEpoch: Number(receivedAtEpoch || 0),
  };
  const { state: appended } = appendEntryIfNew(state, entry);
  // Always advance the activity clock + lifecycle hooks, even when
  // dedupe drops the entry (e.g., backlog replay re-emits an event
  // we already cached). The bubble doesn't get rendered twice but
  // activity tracking still sees the heartbeat — without this, the
  // WorkingIndicator trips its "stalled" threshold during a healthy
  // live stream and only un-trips on tab switch (when remount
  // forces a hydrate that includes a freshly-stamped lastEventAt).
  const next = appended === state ? { ...state } : appended;
  next.lastEventAt = Date.now();
  switch (raw?.type) {
    case CLAUDE_EVENT.ASSISTANT:
      next.turnInFlight = true;
      break;
    case CLAUDE_EVENT.RESULT:
      next.turnInFlight = false;
      next.pendingPermission = null;
      break;
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
      next.pendingPermission = raw;
      break;
    case CLAUDE_EVENT.PERMISSION_RESPONSE: {
      const respondedId = String(raw.request_id || '');
      const pendingId = pendingRequestId(state.pendingPermission);
      if (!respondedId || !pendingId || respondedId === pendingId) {
        next.pendingPermission = null;
      }
      break;
    }
    default:
      break;
  }
  return next;
}

function reduceIncomingHistory(state, raw) {
  const entry = { source: ENTRY_SOURCE.HISTORY, raw };
  const { state: appended, appended: didAppend } = appendEntryIfNew(state, entry);
  if (!didAppend) { return state; }
  const next = appended;
  switch (raw?.type) {
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
      next.pendingPermission = raw;
      break;
    case CLAUDE_EVENT.RESULT:
      next.pendingPermission = null;
      break;
    case CLAUDE_EVENT.PERMISSION_RESPONSE: {
      const respondedId = String(raw.request_id || raw.request?.request_id || '');
      const pendingId = pendingRequestId(state.pendingPermission);
      if (!respondedId || !pendingId || respondedId === pendingId) {
        next.pendingPermission = null;
      }
      break;
    }
    default:
      break;
  }
  return next;
}

function pendingRequestId(pending) {
  if (!pending) { return ''; }
  return String(
    pending.request_id
    || pending.request?.request_id
    || pending.id
    || '',
  );
}

export function useSessionStream(taskId, onIncomingEvent) {
  const [state, dispatch] = useReducer(
    reducer,
    taskId,
    (id) => readCachedState(id),
  );
  const [streamGeneration, setStreamGeneration] = useState(0);
  const taskIdRef = useRef(taskId);

  // Persist every state transition into the module-level cache so a
  // remount (tab switch) sees the latest events when it hydrates.
  useEffect(() => {
    if (state && taskIdRef.current) {
      writeCachedState(taskIdRef.current, state);
    }
  }, [state]);

  useEffect(() => {
    if (!taskId) { return undefined; }
    // Hydrate the reducer from the cache when taskId changes (or on
    // first mount). This is what restores pre-existing entries before
    // the new SSE connection starts replaying — without it, a remount
    // would render an empty list until the server's history catches
    // up.
    taskIdRef.current = taskId;
    dispatch({
      type: ACTION_HYDRATE,
      value: { ...readCachedState(taskId), lifecycle: SESSION_LIFECYCLE.CONNECTING },
    });

    const stream = new EventSource(
      `/api/sessions/${encodeURIComponent(taskId)}/events`,
    );

    stream.addEventListener('session_event', (event) => {
      const payload = safeParseJSON(event.data);
      const envelope = payload?.event || payload;
      const raw = envelope?.raw || envelope;
      if (!raw) { return; }
      dispatch({
        type: ACTION_INCOMING_EVENT,
        event: raw,
        receivedAtEpoch: envelope?.received_at_epoch,
      });
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.STREAMING });
      if (typeof onIncomingEvent === 'function') {
        onIncomingEvent(raw, taskId);
      }
    });
    stream.addEventListener('session_history_event', (event) => {
      const payload = safeParseJSON(event.data);
      const envelope = payload?.event || payload;
      const raw = envelope?.raw || envelope;
      if (!raw) { return; }
      dispatch({ type: ACTION_INCOMING_HISTORY, event: raw });
    });
    stream.addEventListener('session_idle', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.IDLE });
      stream.close();
    });
    stream.addEventListener('session_missing', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.MISSING });
      stream.close();
    });
    stream.addEventListener('session_closed', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      }
    };
    return () => stream.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, streamGeneration]);

  return {
    events: state.events,
    lifecycle: state.lifecycle,
    turnInFlight: state.turnInFlight,
    pendingPermission: state.pendingPermission,
    lastEventAt: state.lastEventAt,
    appendLocalEvent: (event) => dispatch({ type: ACTION_LOCAL_EVENT, event }),
    markTurnBusy: (value) => dispatch({ type: ACTION_MARK_TURN_BUSY, value }),
    dismissPermission: () => dispatch({ type: ACTION_DISMISS_PERMISSION }),
    reconnect: () => setStreamGeneration((n) => n + 1),
  };
}

// Drop the cached chat state for a task — used when the operator
// "forgets" the workspace. Future mounts for that task start fresh.
export function clearTaskStreamCache(taskId) {
  if (!taskId) {
    TASK_STREAM_CACHE.clear();
    return;
  }
  TASK_STREAM_CACHE.delete(taskId);
}
