import { useEffect, useReducer } from 'react';
import { safeParseJSON } from '../utils/sse.js';

export const SESSION_LIFECYCLE = {
  CONNECTING: 'connecting',
  IDLE: 'idle',           // record exists but no live subprocess
  STREAMING: 'streaming', // events flowing
  CLOSED: 'closed',
  MISSING: 'missing',     // server has no record for this task
};

const ACTION_RESET = 'reset';
const ACTION_INCOMING_EVENT = 'incoming_event';
const ACTION_LIFECYCLE = 'lifecycle';
const ACTION_LOCAL_EVENT = 'local_event';
const ACTION_DISMISS_PERMISSION = 'dismiss_permission';
const ACTION_MARK_TURN_BUSY = 'mark_turn_busy';

function initialState() {
  return {
    events: [],
    lifecycle: SESSION_LIFECYCLE.CONNECTING,
    turnInFlight: false,
    pendingPermission: null,
  };
}

function reducer(state, action) {
  switch (action.type) {
    case ACTION_RESET:
      return initialState();
    case ACTION_INCOMING_EVENT:
      return reduceIncomingEvent(state, action.event);
    case ACTION_LOCAL_EVENT:
      return { ...state, events: [...state.events, action.event] };
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

function reduceIncomingEvent(state, raw) {
  const events = [...state.events, { source: 'server', raw }];
  let next = { ...state, events };
  switch (raw?.type) {
    case 'assistant':
      next.turnInFlight = true;
      break;
    case 'result':
      next.turnInFlight = false;
      next.pendingPermission = null;
      break;
    case 'permission_request':
    case 'control_request':
      next.pendingPermission = raw;
      break;
    case 'permission_response': {
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
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  useEffect(() => {
    if (!taskId) { return undefined; }
    dispatch({ type: ACTION_RESET });

    const stream = new EventSource(
      `/api/sessions/${encodeURIComponent(taskId)}/events`,
    );

    stream.addEventListener('session_event', (event) => {
      // Unwrap: { type, event: { event_type, raw: <CLAUDE_EVENT> } }
      const payload = safeParseJSON(event.data);
      const envelope = payload?.event || payload;
      const raw = envelope?.raw || envelope;
      if (!raw) { return; }
      dispatch({ type: ACTION_INCOMING_EVENT, event: raw });
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.STREAMING });
      if (typeof onIncomingEvent === 'function') {
        onIncomingEvent(raw, taskId);
      }
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
  }, [taskId]);

  return {
    events: state.events,
    lifecycle: state.lifecycle,
    turnInFlight: state.turnInFlight,
    pendingPermission: state.pendingPermission,
    appendLocalEvent: (event) => dispatch({ type: ACTION_LOCAL_EVENT, event }),
    markTurnBusy: (value) => dispatch({ type: ACTION_MARK_TURN_BUSY, value }),
    dismissPermission: () => dispatch({ type: ACTION_DISMISS_PERMISSION }),
  };
}
