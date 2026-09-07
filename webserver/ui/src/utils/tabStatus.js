import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { cx } from './cx.js';

// Single source of truth for the per-task WORKSPACE status.
//
// This axis is the workspace/task state — active / provisioning / review /
// done / terminated / errored, plus the "no saved session yet" IDLE. It does
// NOT decide agent liveness: that is `deriveAgentStatus` in ./agentStatus.js,
// the one place allowed to say WORKING.
//
// It USED to answer WORKING off the 5s-polled ``session.working`` field, and
// that made it a second, competing source of agent status. Because
// ``deriveAgentStatus`` falls through to this value for the dot whenever the
// live kind isn't itself WORKING/WORKFLOW/APPROVAL, a stale polled flag could
// paint the dot and the tooltip "Claude is working" while the live store —
// and the in-chat working indicator, which reads ``turnInFlight`` — agreed
// nothing was running. That is the reported "it says working when it isn't,
// but there's no working animation".
//
// Inputs:
//   - session: the session record from /api/sessions
//   - needsAttention: whether the task is currently asking for input
//     (permission_request / control_request) — sourced from
//     `useTaskAttention()`, which lives once at the App.jsx top level.
export function deriveTabStatus(session) {
  const status = session?.status || TAB_STATUS.ACTIVE;
  if (status === TAB_STATUS.ACTIVE
      && session?.live === false
      && !session?.[AGENT_SESSION_ID]) {
    return TAB_STATUS.IDLE;
  }
  return status;
}

// Final status with attention override. Always use this from rendering
// components (Tab, SessionHeader, anywhere else that paints a dot).
export function resolveTabStatus(session, needsAttention) {
  if (needsAttention) { return TAB_STATUS.ATTENTION; }
  return deriveTabStatus(session);
}

// The status-dot className. Tab and SessionHeader both paint the
// same dot — one ``status-dot`` base, a ``status-<status>`` colour
// class, plus the provisioning ``is-loading`` and the
// ``is-idle-alive`` modifiers. ``idleAlive`` is passed in by the
// caller so each surface keeps its own derivation (SessionHeader
// additionally factors in ``!turnInFlight``).
export function statusDotClass(status, { isLoading = false, idleAlive = false } = {}) {
  return cx(
    'status-dot',
    `status-${status}`,
    isLoading && 'is-loading',
    idleAlive && 'is-idle-alive',
  );
}

// Tooltip text for the WORKSPACE status only.
//
// No WORKING branch: agent liveness is not this module's axis, and the
// hardcoded "Claude is working" it used to return was wrong twice over — it
// named Claude on a Codex tab, and it fired off the polled flag even when the
// live store said the turn was over. Surfaces that want to describe the AGENT
// render ``deriveAgentStatus(...).title``, which knows both the live state and
// the backend's name.
export function tabStatusTitle(baseStatus, needsAttention = false) {
  if (needsAttention) { return `${baseStatus} — needs your input`; }
  if (baseStatus === TAB_STATUS.IDLE) {
    return 'no saved agent session — kato will start one when work arrives';
  }
  if (baseStatus === TAB_STATUS.PROVISIONING) {
    return 'provisioning workspace…';
  }
  return baseStatus;
}
