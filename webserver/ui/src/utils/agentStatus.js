import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { AGENT_STATUS_KIND } from '../constants/agentStatusKind.js';
import { deriveTabStatus, resolveTabStatus, statusDotClass } from './tabStatus.js';

// THE single source of truth for agent (Claude/Codex) liveness.
//
// One function derives it so the header chip, the tab dot, and the tab tooltip
// badge can never disagree (UNA-2492: chip said "closed" while the tab said
// "working"). When the active task's live SSE state is available (``liveStatus``
// from agentStatusStore) it wins — only it can tell ``sleeping`` from
// ``closed``. Otherwise we fall back to the 5s-polled session fields (the old
// per-surface behaviour), best-effort.
//
// This is agent-SUBPROCESS liveness only. The comment-run status (``kato_status``
// WORKING/PENDING on review comments) and the workspace/task status
// (active/review/done) are different axes — they are NOT derived here.

// kind → { label (chip word), title (tooltip) }. Ported verbatim from the old
// SessionHeader.describeClaudeStatus so the chip text/classes are unchanged.
// kind → { label, title(agentName) }. The titles take the agent's NAME: a
// task shows a chip per backend now, and a tooltip saying "Claude is
// processing the current turn" on the Codex chip names the wrong agent.
const STATUS_BY_KIND = {
  [AGENT_STATUS_KIND.PROVISIONING]: { label: 'provisioning', title: () => 'Workspace is being set up.' },
  [AGENT_STATUS_KIND.WORKING]: { label: 'working', title: (n) => `${n} is processing the current turn.` },
  [AGENT_STATUS_KIND.WORKFLOW]: { label: 'workflow', title: (n) => `A background workflow is running — ${n} will report back when it finishes.` },
  [AGENT_STATUS_KIND.APPROVAL]: { label: 'approval', title: (n) => `${n} is paused waiting for your approval.` },
  [AGENT_STATUS_KIND.IDLE]: { label: 'idle', title: (n) => `${n} is connected and waiting for input.` },
  [AGENT_STATUS_KIND.CONNECTING]: { label: 'connecting', title: (n) => `Connecting to the ${n} session…` },
  [AGENT_STATUS_KIND.SLEEPING]: { label: 'sleeping', title: (n) => `No live subprocess — kato will respawn ${n} on the next message.` },
  [AGENT_STATUS_KIND.CLOSED]: { label: 'closed', title: (n) => `The ${n} subprocess for this task has ended.` },
  [AGENT_STATUS_KIND.MISSING]: { label: 'no record', title: () => 'No record for this task on the server.' },
  [AGENT_STATUS_KIND.UNKNOWN]: { label: '—', title: (n) => `${n} status unknown.` },
};

const KIND_BY_LIFECYCLE = {
  [SESSION_LIFECYCLE.STREAMING]: AGENT_STATUS_KIND.IDLE,
  [SESSION_LIFECYCLE.CONNECTING]: AGENT_STATUS_KIND.CONNECTING,
  [SESSION_LIFECYCLE.IDLE]: AGENT_STATUS_KIND.SLEEPING,
  [SESSION_LIFECYCLE.CLOSED]: AGENT_STATUS_KIND.CLOSED,
  [SESSION_LIFECYCLE.MISSING]: AGENT_STATUS_KIND.MISSING,
};

// Live (active-task) path — ported from describeClaudeStatus's precedence.
function liveKind(liveStatus, baseStatus, needsAttention) {
  if (baseStatus === TAB_STATUS.PROVISIONING) { return AGENT_STATUS_KIND.PROVISIONING; }
  // An in-flight turn is the foreground "working" state.
  if (liveStatus.turnInFlight) { return AGENT_STATUS_KIND.WORKING; }
  // ``awaitingBackground`` = turn closed but the agent is blocked on a
  // background wait it scheduled (Monitor / Workflow / run_in_background) —
  // still busy, not idle. A background WORKFLOW gets its own status/colour
  // so the operator can see "something is churning in the background".
  if (liveStatus.awaitingBackground) {
    return liveStatus.backgroundIsWorkflow
      ? AGENT_STATUS_KIND.WORKFLOW
      : AGENT_STATUS_KIND.WORKING;
  }
  if (needsAttention) { return AGENT_STATUS_KIND.APPROVAL; }
  return KIND_BY_LIFECYCLE[liveStatus.lifecycle] || AGENT_STATUS_KIND.UNKNOWN;
}

// Workspace states where the subprocess is gone for good — the task is
// finished/stopped, so kato won't lazily respawn it. A non-live tab in one of
// these reads as ``closed`` rather than ``sleeping``.
const TERMINAL_STATUSES = new Set([
  TAB_STATUS.DONE,
  TAB_STATUS.TERMINATED,
  TAB_STATUS.ERRORED,
]);

// Polled-fallback path (background tabs, no live SSE) — ported from
// Tab.claudeBadge (+ provisioning). A non-live tab is ``closed`` when the task
// is in a terminal state (done/terminated/errored — no respawn), else
// ``sleeping`` because kato lazily ``--resume``s any other tab on the next
// message. The transient post-exit "closed" flash an ACTIVE tab's live stream
// shows is not a pollable fact, so background tabs can't reproduce it — and
// don't need to (that tab will respawn on the next message → sleeping).
function polledKind(session, baseStatus) {
  if (baseStatus === TAB_STATUS.PROVISIONING) { return AGENT_STATUS_KIND.PROVISIONING; }
  if (session?.working === true) { return AGENT_STATUS_KIND.WORKING; }
  if (session?.has_pending_permission) { return AGENT_STATUS_KIND.APPROVAL; }
  if (session?.live === true) { return AGENT_STATUS_KIND.IDLE; }
  if (session?.live === false) {
    return TERMINAL_STATUSES.has(baseStatus)
      ? AGENT_STATUS_KIND.CLOSED
      : AGENT_STATUS_KIND.SLEEPING;
  }
  return AGENT_STATUS_KIND.UNKNOWN;
}

// The only tab-tooltip badge CSS classes that exist are
// is-work/flow/idle/sleep/wait.
const BADGE_KIND = {
  [AGENT_STATUS_KIND.WORKING]: 'work',
  [AGENT_STATUS_KIND.WORKFLOW]: 'flow',
  [AGENT_STATUS_KIND.IDLE]: 'idle',
  [AGENT_STATUS_KIND.CONNECTING]: 'idle',
  [AGENT_STATUS_KIND.SLEEPING]: 'sleep',
  [AGENT_STATUS_KIND.CLOSED]: 'sleep',
  [AGENT_STATUS_KIND.APPROVAL]: 'wait',
};

// Map a status kind to the tooltip badge's ``is-*`` class. Returns '' for kinds
// with no badge styling (provisioning/missing/unknown) — callers treat '' as
// "no badge", matching the old claudeBadge returning null.
export function badgeKindFor(kind) {
  return BADGE_KIND[kind] || '';
}

function dotStatusForKind(kind, resolved) {
  if (kind === AGENT_STATUS_KIND.WORKING) { return TAB_STATUS.WORKING; }
  if (kind === AGENT_STATUS_KIND.WORKFLOW) { return TAB_STATUS.WORKFLOW; }
  if (kind === AGENT_STATUS_KIND.APPROVAL) { return TAB_STATUS.ATTENTION; }
  return resolved;
}

// session: the polled /api/sessions record. liveStatus: the active task's live
// SSE facts {lifecycle, turnInFlight, pendingPermission} from agentStatusStore.
// needsAttention: the caller's attention flag.
// Returns { kind, label, title, dotClass } — kind drives the chip and the dot.
export function deriveAgentStatus(
  session, liveStatus = null, needsAttention = false, agentName = '',
) {
  const baseStatus = deriveTabStatus(session);
  const kind = liveStatus
    ? liveKind(liveStatus, baseStatus, needsAttention)
    : polledKind(session, baseStatus);
  const meta = STATUS_BY_KIND[kind] || STATUS_BY_KIND[AGENT_STATUS_KIND.UNKNOWN];

  const resolved = resolveTabStatus(session, needsAttention);
  const dotStatus = dotStatusForKind(kind, resolved);
  const turnish = liveStatus ? !!liveStatus.turnInFlight : (session?.working === true);
  const idleAlive = dotStatus === TAB_STATUS.ACTIVE
    && !turnish
    && session?.working === false;
  const dotClass = statusDotClass(dotStatus, {
    isLoading: baseStatus === TAB_STATUS.PROVISIONING,
    idleAlive,
  });

  const name = String(agentName || '').trim() || 'The agent';
  return {
    kind, label: meta.label, title: meta.title(name), dotClass, status: dotStatus,
  };
}
