import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AgentBackendTabs from './AgentBackendTabs.jsx';
import AgentBackendSetup from './AgentBackendSetup.jsx';
import { backendLabel } from './AgentBackendChip.jsx';
import { useActiveBackend } from '../stores/activeBackendStore.js';
import { createPortal } from 'react-dom';
import ChatSearch from './ChatSearch.jsx';
import EventLog from './EventLog.jsx';
import MessageForm from './MessageForm.jsx';
import QueuedMessageList from './QueuedMessageList.jsx';
import PanelCard from './PanelCard.jsx';
import SessionHeader, { SessionHeaderPlaceholder } from './SessionHeader.jsx';
import WorkingIndicator from './WorkingIndicator.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { useSessionStream, SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { agentStatusStore } from '../stores/agentStatusStore.js';
import {
  readQueuedMessages,
  writeQueuedMessages,
  persistQueuedMessages,
  hydrateQueuedMessages,
} from '../utils/queuedMessagesStore.js';
import { readSteerWhileWorking } from '../utils/composerSteerPref.js';
import { useSessionOption } from '../hooks/useSessionOption.js';
import { permissionStore } from '../stores/permissionStore.js';
import { usePendingPermissions } from '../hooks/usePendingPermissions.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';
import { toast } from '../stores/toastStore.js';
import { fetchEffortLevels, fetchModels, fetchSessionAgentMode, fetchSessionEffort, fetchSessionModel, fetchSessionRemoteControl, postChatMessage, postSession, setSessionAgentMode, setSessionEffort, setSessionModel, setSessionRemoteControl,
  refreshAgentBackends,
} from '../api.js';
import { useContextUsage } from '../hooks/useContextUsage.js';
import { useBusyAction } from '../hooks/useBusyAction.js';

// Grace before we reconnect a still-"live" stream that the server says has a
// pending permission we haven't received. Long enough for a normal live event
// to arrive (no needless reset on the common race), short enough that a
// silently-dropped EventSource self-heals without a manual page refresh.
const PERMISSION_RECONNECT_GRACE_MS = 2000;

export default function SessionDetail({
  session,
  onActivity,
  onPendingPermissionChange,
  needsAttention = false,
  composerRef = null,
  onResizePointerDown,
  onOpenFile,
  onRegisterReconnect,
  onWorkspaceMutated,
  planAvailable = false,
  onOpenPlan,
}) {
  const taskId = session?.task_id;
  const stream = useSessionStream(taskId, onActivity);

  useEffect(() => {
    if (typeof onRegisterReconnect === 'function') {
      onRegisterReconnect(stream.reconnect);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.reconnect]);

  // Pending-permission truth for THIS task from the shared store (the
  // reliable, poll-backed source) OR the focused stream's live event.
  // Used for the "waiting for approval" indicator + status so they never
  // fall back to "taking too long" just because the single SSE frame was
  // buffered/dropped — the bug that forced a page refresh.
  const pendingPermissions = usePendingPermissions();
  const hasPendingPermission = (
    !!stream.pendingPermission
    || pendingPermissions.list.some(
      (ask) => unpackPermissionEnvelope(ask).taskId === taskId,
    )
  );

  // Feed the focused task's live ``control_request`` into the shared store
  // so its dialog (owned globally by GlobalPermissionContainer) pops
  // instantly; the store's poll is the reliable fallback when the SSE
  // frame never lands.
  useEffect(() => {
    if (stream.pendingPermission) {
      permissionStore.push(
        taskId, stream.pendingPermission, session?.task_summary || '',
      );
    }
  }, [taskId, stream.pendingPermission, session?.task_summary]);

  // Let the globally-owned modal drop its approve/deny audit bubble into
  // THIS chat while it's mounted.
  useEffect(
    () => permissionStore.registerAuditSink(taskId, stream.appendLocalEvent),
    [taskId, stream.appendLocalEvent],
  );

  // Publish this (active) task's live agent status into the shared store, so
  // the tab dot/badge derive from the SAME live value as the header chip
  // instead of the laggy polled fields (UNA-2492). Scalar deps + the store's
  // shallow-equal short-circuit keep this from looping.
  useEffect(() => {
    agentStatusStore.setStatus(taskId, {
      lifecycle: stream.lifecycle,
      turnInFlight: stream.turnInFlight,
      awaitingBackground: stream.awaitingBackground,
      backgroundIsWorkflow: stream.backgroundIsWorkflow,
      pendingPermission: hasPendingPermission,
    });
  }, [
    taskId, stream.lifecycle, stream.turnInFlight,
    stream.awaitingBackground, stream.backgroundIsWorkflow, hasPendingPermission,
  ]);

  // Drop this task's live entry when the active tab changes. SessionDetail is
  // keyed per task, so unmount fires for the OLD task; clearStatus removes only
  // that key (other tasks never had a live entry). The tab then falls back to
  // polled status — correct, since its stream is gone.
  useEffect(() => {
    return () => { agentStatusStore.clearStatus(taskId); };
  }, [taskId]);

  // The task header (title + action buttons + Claude status + chat
  // search) is hoisted into a full-width bar UNDER the tab strip and
  // ABOVE all three panels. ``#task-header-slot`` is rendered by
  // Layout; we keep ALL wiring (stream, message handlers, search
  // state) here and only PORTAL the rendered header into that slot —
  // nothing is lifted, so the permission-dialog auto-reconnect, the
  // composer queue and the search highlighting stay owned by
  // SessionDetail. Falls back to rendering the header inline (its old
  // in-pane position) when the slot isn't in the DOM (unit tests /
  // the legacy sidebar shell).
  const [headerSlot, setHeaderSlot] = useState(null);
  // Readiness of the agent tab currently selected, reported up by
  // <AgentBackendTabs> (only it sees the probe). ``null`` until the probe
  // answers — treated as READY so a working chat never flashes a setup
  // panel while the lookup is in flight.
  const [activeBackendEntry, setActiveBackendEntry] = useState(null);
  // Bumped by "Check again": remounting the tab strip re-runs the probe.
  const [backendProbeNonce, setBackendProbeNonce] = useState(0);
  const [rechecking, setRechecking] = useState(false);
  // Either half means there is no chat to show: the CLI is missing, or it
  // works but kato has no manager wired for it yet (needs a restart).
  // One name for every agent-facing string in this component.
  // The tab the operator is ON — the store's value when the tab strip has
  // reported in, the record's otherwise (correct on first paint). NOT
  // ``session.agent_backend`` directly: that is polled and lags a switch.
  const activeBackend = useActiveBackend(taskId, session?.agent_backend || '');
  const agentName = backendLabel(activeBackend) || 'the agent';
  const backendUnready = !!activeBackendEntry
    && activeBackendEntry.chat_available === false;

  const recheckBackends = useCallback(async () => {
    setRechecking(true);
    try {
      // The server memoises the probe for a minute; this asks it to forget
      // so an operator who JUST installed the CLI isn't told to wait.
      await refreshAgentBackends();
    } catch {
      // A failed refresh still gets a re-probe below — the cached answer is
      // simply a minute stale, which is not worth surfacing as an error.
    } finally {
      setBackendProbeNonce((n) => n + 1);
      setRechecking(false);
    }
  }, []);
  useEffect(() => {
    setHeaderSlot(
      (typeof document !== 'undefined'
        && document.getElementById('task-header-slot')) || null,
    );
  }, []);

  // Outgoing message queue. While Claude is mid-turn the operator's
  // messages are HELD by default and flushed one at a time as each
  // turn ends (see ``onSendMessage`` + the flush effect). The queue
  // is now **state** (not a ref) so the floating <QueuedMessageList>
  // above the composer can render the pending items and let the
  // operator remove, reorder mentally, or "Steer" — i.e. deliver
  // immediately without waiting for the current turn to finish.
  //
  // ``queuedMessagesRef`` mirrors the state so the turn-end flush
  // effect can read the latest list without re-subscribing on every
  // queue mutation (the effect depends only on ``turnInFlight``).
  // SessionDetail is keyed per task (App.jsx), so it REMOUNTS on tab switch
  // and React drops this state. Seed from the per-task queue store and mirror
  // every change back to it, so the operator's queued/steer messages survive
  // switching away and back (they used to vanish). The store is keyed by task,
  // so task A's queue never leaks into task B.
  const [queuedMessages, setQueuedMessages] = useState(() => readQueuedMessages(taskId));
  const queuedMessagesRef = useRef(queuedMessages);
  // "The live queue owns the durable backup": set true once the async reload-
  // hydrate has run OR the operator has changed the queue (enqueue / steer /
  // remove / edit / turn-end drain), whichever comes first. Gates the IDB write
  // so the empty pre-hydrate state can't wipe a stored queue; and makes a slow
  // IDB read that resolves AFTER an operator drain refuse to re-apply (else an
  // in-flight read would resurrect a just-sent/removed message).
  const queueSettledRef = useRef(false);
  // Wrap every operator queue mutation so it both updates state AND marks the
  // queue settled (the durable backup is now authoritative for this mount).
  const commitQueue = useCallback((updater) => {
    queueSettledRef.current = true;
    setQueuedMessages(updater);
  }, []);
  useEffect(() => {
    queuedMessagesRef.current = queuedMessages;
    writeQueuedMessages(taskId, queuedMessages); // sync Map (instant tab switch)
    if (queueSettledRef.current) {
      persistQueuedMessages(taskId, queuedMessages); // durable IDB (survives reload, incl. images)
    }
  }, [taskId, queuedMessages]);
  // Restore the queue (incl. any pasted images) from IndexedDB after a full
  // page reload, when the in-memory store is cold. A warm store (ordinary tab
  // switch) already supplied the items via the useState initializer above and
  // wins; and if the operator already touched the queue while this read was in
  // flight (queueSettledRef), we don't clobber the live state.
  useEffect(() => {
    let cancelled = false;
    hydrateQueuedMessages(taskId).then((items) => {
      if (cancelled || queueSettledRef.current) { return; }
      if (items.length > 0 && queuedMessagesRef.current.length === 0) {
        setQueuedMessages(items);
      }
      queueSettledRef.current = true;
    });
    return () => { cancelled = true; };
  }, [taskId]);
  const prevTurnInFlightRef = useRef(false);
  // Seed the turn-flight tracker for this mount (the queue itself is restored
  // by the lazy initializer above, not cleared).
  useEffect(() => {
    prevTurnInFlightRef.current = stream.turnInFlight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // Model + effort selectors share one fetch-list / fetch-current /
  // on-change state block (see useSessionOption). The model catalogue
  // and the effort levels are both discovered from the agent CLI (not
  // hardcoded); they differ only in the API fns and result keys.
  const [availableModels, selectedModel, handleModelChange] = useSessionOption(
    taskId,
    {
      fetchOptions: fetchModels,
      optionsKey: 'models',
      fetchCurrent: fetchSessionModel,
      currentKey: 'model',
      setCurrent: setSessionModel,
    },
  );
  const [effortLevels, selectedEffort, handleEffortChange, effortDefault] = useSessionOption(
    taskId,
    {
      fetchOptions: fetchEffortLevels,
      optionsKey: 'levels',
      fetchCurrent: fetchSessionEffort,
      currentKey: 'effort',
      setCurrent: setSessionEffort,
      // ``/api/effort-levels`` carries the concrete default kato falls back
      // to (no more "Auto"); the picker shows it when the task has no override.
      defaultKey: 'default',
    },
  );
  // Plan mode is NOT tracked separately here. It used to be: a ``planMode``
  // boolean with its own fetch, its own setter, its own endpoint — and a line
  // in handleAgentModeChange hand-syncing it against ``agentMode``, because
  // the two described the same server-side override through two clients. The
  // composer read only ``agentMode``, so the boolean was written and never
  // read. ``agentMode === 'plan'`` is the whole of it.
  const contextUsage = useContextUsage(taskId, stream.turnInFlight);

  const [agentMode, setAgentMode] = useState('');
  useEffect(() => {
    if (!taskId) { setAgentMode(''); return; }
    fetchSessionAgentMode(taskId)
      .then((result) => setAgentMode(String((result && result.mode) || '')))
      .catch(() => {});
  }, [taskId]);
  // Remote Control — the composer's ``/`` menu toggle that hands this task's
  // live Claude session to claude.ai / the Claude app. Server-owned state
  // (whether the CLI supports it, whether a subprocess is bridged right now),
  // so this holds the last answer rather than a local boolean, and re-reads
  // it after every write. The row is hidden entirely unless the server says
  // the task's agent supports it — see MessageForm.
  // Held in the SERVER's shape (``supported``/``enabled``/``live``/
  // ``session_url``) plus two client-only fields, so a response can be
  // merged onto it without a translation layer that has to be right in
  // both directions.
  // Bumped whenever the operator says something. EventLog re-arms its
  // sticky-scroll intent on the change — reusing the ONE pin flag it already
  // has rather than adding a second scrolling mechanism beside it.
  const [pinRequestId, setPinRequestId] = useState(0);
  const [remoteControl, setRemoteControl] = useState(null);
  const remoteControlTaskRef = useRef('');
  useEffect(() => {
    if (!taskId) { setRemoteControl(null); return; }
    if (remoteControlTaskRef.current !== taskId) {
      // A different task's bridge (and its URL) must never be on screen for
      // this one, not even for the length of a fetch.
      remoteControlTaskRef.current = taskId;
      setRemoteControl(null);
    }
    // Re-read on every LIFECYCLE change, not just on the task. ``live`` is a
    // fact about a subprocess this pane does not own: switching the toggle on
    // an idle tab stores a preference and bridges nothing — the bridge is
    // built later, by the spawn the operator's next message triggers. Keyed
    // on ``taskId`` alone, the row went on saying "connects when you send
    // your next message" for the rest of the session, long after it had, and
    // the link to the other device never appeared at all.
    fetchSessionRemoteControl(taskId)
      .then((result) => setRemoteControl((prev) => {
        // Drop an answer the operator has already moved past: a toggle still
        // in flight owns the state, and so does a task switch mid-fetch.
        if (prev && prev.busy) { return prev; }
        if (remoteControlTaskRef.current !== taskId) { return prev; }
        return result || null;
      }))
      // Keep the last good answer on a transient failure — losing it would
      // make the whole row disappear from the menu.
      .catch(() => {});
  }, [taskId, stream.lifecycle]);
  const handleRemoteControlChange = useCallback(async (on) => {
    // Optimistic on the switch position only — never on ``live`` or the URL,
    // which are the server's answer about a subprocess this tab cannot see.
    // Claiming a bridge before one exists is the one thing this toggle must
    // not do. ``busy`` locks the select so a double-click can't race.
    setRemoteControl((prev) => (
      prev ? { ...prev, enabled: on, busy: true, error: '' } : prev
    ));
    const result = await setSessionRemoteControl(taskId, on);
    const ok = !!(result && result.ok);
    const body = (result && result.body) || {};
    setRemoteControl((prev) => ({
      ...(prev || {}),
      ...body,
      // A failed request answers with an empty body, and taking that
      // literally would report the feature unsupported and make the whole
      // row vanish mid-click. Keep what we already knew, and put the switch
      // back where the server actually left it.
      enabled: body.enabled === undefined ? !on : !!body.enabled,
      error: ok
        ? ''
        : body.error || (result && result.error) || 'could not reach kato',
      busy: false,
    }));
  }, [taskId]);

  const handleAgentModeChange = useCallback((mode) => {
    const next = String(mode ?? '');
    setAgentMode(next);
    setSessionAgentMode(taskId, next);
  }, [taskId]);
  useEffect(() => {
    if (typeof onPendingPermissionChange !== 'function') { return; }
    onPendingPermissionChange(taskId, hasPendingPermission);
  }, [taskId, hasPendingPermission, onPendingPermissionChange]);

  // Auto-reconnect when a permission request lands while we're
  // already sitting on this tab but the per-task SSE was closed.
  //
  // ``useSessionStream`` closes the EventSource on ``session_idle``
  // (resource optimisation while Claude sleeps). If a permission
  // request then arrives, the app-wide status feed still flags the
  // tab (``needsAttention`` → gold), but THIS stream is dead so
  // ``stream.pendingPermission`` never updates and the decision
  // dialog never appears — the operator had to click the tab again
  // to force a remount/reconnect even though they were already here.
  //
  // Re-open the stream once per attention period when the session is
  // sleeping and nothing is pending yet. ``needsAttention`` can turn
  // true while the cached lifecycle still says STREAMING; if it flips
  // to IDLE a moment later, the dialog still needs to pop immediately.
  const permissionReconnectAttemptedRef = useRef(false);
  useEffect(() => {
    permissionReconnectAttemptedRef.current = false;
  }, [taskId]);
  useEffect(() => {
    if (!needsAttention || stream.pendingPermission) {
      permissionReconnectAttemptedRef.current = false;
      return undefined;
    }
    if (permissionReconnectAttemptedRef.current) { return undefined; }
    const sleeping = (
      stream.lifecycle === SESSION_LIFECYCLE.IDLE
      || stream.lifecycle === SESSION_LIFECYCLE.CLOSED
      || stream.lifecycle === SESSION_LIFECYCLE.MISSING
    );
    if (sleeping) {
      // Stream was closed on idle — reopen now to replay the pending ask.
      permissionReconnectAttemptedRef.current = true;
      stream.reconnect();
      return undefined;
    }
    // Stream still marked live, but the server says a permission is pending and
    // we never received it — the EventSource likely dropped silently. Give the
    // live event a short grace, then reconnect once to replay the backlog (what
    // a manual page refresh used to do — the "dialog feels stuck" report).
    const handle = window.setTimeout(() => {
      permissionReconnectAttemptedRef.current = true;
      stream.reconnect();
    }, PERMISSION_RECONNECT_GRACE_MS);
    return () => window.clearTimeout(handle);
    // stream.reconnect is a fresh closure each render; intentionally
    // excluded so this fires on the attention/lifecycle change only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsAttention, stream.pendingPermission, stream.lifecycle]);

  // Drag handle for the chat column's width, rendered by PanelCard on
  // the card's left edge (it anchors into the layout gutter, so the
  // card must not clip it — see PanelCard).
  const panelProps = {
    as: 'main',
    id: 'session-pane',
    resizerId: 'right-pane-resizer',
    onResizePointerDown,
  };

  if (!session) {
    return (
      <PanelCard {...panelProps}>
        {/* Keep the global header bar present (with a "Select a
            task" title + inert buttons) instead of letting it vanish
            — a header that appears/disappears as you click around is
            jarring and shifts the layout. */}
        {headerSlot
          ? createPortal(<SessionHeaderPlaceholder />, headerSlot)
          : <SessionHeaderPlaceholder />}
        <section id="session-placeholder" className="placeholder">
          Select a tab to chat with the bound Claude session.
        </section>
      </PanelCard>
    );
  }

  // Actually deliver a message to Claude now. Optimistic local USER
  // bubble + POST + result handling. The server echoes the user
  // event back shortly after; dedupe (MessageFilter.dedupeUserEchoes)
  // collapses the local + server pair. Image attachments surface via
  // ``imageCount`` so the renderer can suffix "(N attached)" without
  // polluting the dedupe key.
  async function deliverMessage(text, images = []) {
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text,
      imageCount: images.length,
    });
    stream.markTurnBusy(true);
    const result = await postChatMessage(
      taskId, text, images, activeBackend,
    );
    if (result.ok) {
      const status = result.body?.status;
      if (status === 'spawned') {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM,
          text: `✓ resumed — spawning ${agentName}…`,
        });
        stream.reconnect();
      } else {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✓ delivered',
        });
      }
      return true;
    }
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR,
      text: `send failed: ${result.error}`,
    });
    stream.markTurnBusy(false);
    // Return false so MessageForm preserves the operator's draft —
    // they can edit + retry instead of having to retype.
    return false;
  }

  // Composer entry point. When Claude is idle, always deliver immediately.
  // While Claude is mid-turn, the behavior depends on the operator's
  // "steer while working" preference (Settings → Chat):
  //   * steer ON (default) — HOLD the message in the queue and let it fly
  //     when the turn finishes (the flush effect below). The queue is visible
  //     via <QueuedMessageList>; the operator can remove/edit items or click
  //     "Steer" to promote one mid-turn.
  //   * steer OFF — deliver it to the live session IMMEDIATELY, mid-turn,
  //     exactly like Claude Code in VS Code (Claude reads it on its next pump
  //     while still working). No queue, no wait.
  async function onSendMessage(text, images = []) {
    // Sending is the operator SPEAKING, which is the clearest possible
    // "show me the bottom" intent — clearer even than scrolling there.
    // Bumped for the queued path too: a queued message appears in the list
    // under the composer, and it is no more use off-screen than a sent one.
    setPinRequestId((n) => n + 1);
    if (stream.turnInFlight && readSteerWhileWorking()) {
      commitQueue((prev) => [
        ...prev,
        { id: _newQueuedId(), text, images, queuedAt: Date.now() },
      ]);
      // Truthy → MessageForm accepts it and clears the draft.
      // The visible queue list (and the queued tooltip on the send
      // button) replace the earlier transient "queued" bubble.
      return true;
    }
    // Idle, or steer disabled → send now (mid-turn injection when working).
    return deliverMessage(text, images);
  }

  // Operator clicked the trash icon on a queued row → forget it
  // entirely. Safe whether the turn is in-flight or not.
  function removeQueuedMessage(id) {
    commitQueue((prev) => prev.filter((item) => item.id !== id));
  }

  // Operator edited a queued row's text in place (Edit affordance) →
  // update just that item's text, preserving its id/images/queue
  // position so a revised steer message keeps its place in line
  // instead of forcing a delete-and-retype.
  function editQueuedMessage(id, text) {
    commitQueue((prev) => prev.map(
      (item) => (item.id === id ? { ...item, text } : item),
    ));
  }

  // Operator clicked "Steer" on a queued row → deliver it NOW even
  // if Claude is mid-turn. The Claude CLI accepts mid-turn
  // ``send_user_message`` envelopes (the streaming session writes
  // straight to stdin) so the agent will read it on the next pump.
  // We drop the item from the queue regardless of whether the
  // delivery succeeds — operators who want to retry can retype.
  //
  // Reads the target via ``queuedMessagesRef`` (not via a setState
  // callback) because React batches state-update callbacks and they
  // run AFTER our early-return check would; the ref is the
  // synchronously-current snapshot of what's queued right now.
  async function steerQueuedMessage(id) {
    const target = (queuedMessagesRef.current || []).find(
      (item) => item.id === id,
    );
    if (!target) { return; }
    commitQueue((prev) => prev.filter((item) => item.id !== id));
    await deliverMessage(target.text, target.images);
  }

  // A chat switch is being requested (ChatsMenu POSTed /chats). Killing the
  // live subprocess flips ``turnInFlight`` true→false — the same falling
  // edge a NORMAL turn end produces — and the SSE ``session_closed`` from
  // the kill can land before the POST even resolves. Without this flag the
  // flush effect below would treat the kill as a turn end and deliver a
  // queued message written for the OLD conversation straight into the
  // fresh/resumed chat as its opener. Armed BEFORE the request fires;
  // cleared on completion (onChatChanged) or on failure (ChatsMenu).
  const chatSwitchPendingRef = useRef(false);
  const onChatSwitchPending = useCallback((pending) => {
    chatSwitchPendingRef.current = !!pending;
  }, []);

  // Flush the queue one message at a time as each turn ends.
  // Delivering a queued message re-enters the busy state, so the
  // next one waits for the turn after — messages stay strictly
  // ordered without ever interrupting Claude (unless the operator
  // explicitly steers).
  useEffect(() => {
    const wasInFlight = prevTurnInFlightRef.current;
    prevTurnInFlightRef.current = stream.turnInFlight;
    if (wasInFlight && !stream.turnInFlight
        && !chatSwitchPendingRef.current
        && queuedMessagesRef.current.length > 0) {
      const next = queuedMessagesRef.current[0];
      commitQueue((prev) => prev.filter((item) => item.id !== next.id));
      deliverMessage(next.text, next.images);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.turnInFlight]);

  // Composer Stop — same action the header's Stop runs, reusing its result
  // handler so both surfaces report the outcome identically. Offered only
  // while Claude is working and there is nothing to send (see MessageForm).
  const [, onComposerStop] = useBusyAction(
    () => postSession(taskId, 'stop'),
    { onDone: (result) => onStopped(result) },
  );

  async function onStopped(result) {
    stream.appendLocalEvent(
      result.ok
        ? { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✗ session stopped' }
        : { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR, text: `stop failed: ${result.error}` },
    );
  }

  // Resume: respawn the Claude subprocess and tell it to keep going.
  // We send a real message ("Please continue…") rather than a no-op so
  // Claude has something to react to — the spawn path requires a user
  // turn to anchor the resumed conversation. Delivered directly, NOT
  // via the queue: resume must always actually send (a session being
  // resumed is idle, and a queued resume would never flush).
  async function onResume() {
    await deliverMessage('Please continue from where you left off.');
  }

  // Drop a system bubble into the chat so the operator has a visual
  // confirmation that adoption took — without it, the modal closes,
  // a toast flashes, and the chat looks unchanged. The bubble also
  // persists in the per-task event cache, so switching tabs and
  // coming back still shows "session attached" until the next
  // server-side history replay overwrites the picture.
  function onSessionAdopted(adopted) {
    const sessionId = String(adopted?.[AGENT_SESSION_ID] || '').trim();
    const cwd = String(adopted?.cwd || '').trim();
    const idShort = sessionId ? `${sessionId.slice(0, 8)}…` : '(unknown)';
    const cwdLine = cwd ? `\ncwd: ${cwd}` : '';
    stream.appendLocalEvent({
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.SYSTEM,
      text: (
        `📎 session attached — kato will resume ${agentName} session ${idShort} `
        + `for ${taskId} on the next message.${cwdLine}`
      ),
    });
  }

  // Fresh chat / chat switch (ChatsMenu in the header): wipe the rendered
  // transcript and reconnect — the SSE then replays the now-active chat's
  // history (nothing, for a brand-new chat) into the clean slate. The old
  // conversation stays navigable from the chats menu.
  //
  // Queued composer messages were written as follow-ups to the conversation
  // the operator just LEFT — auto-flushing them would make a stale
  // instruction the opener of the new chat. Discard them, echoing the texts
  // in a bubble so nothing is silently lost. The switch itself is confirmed
  // by the ChatsMenu toast; a "switched" bubble is deliberately NOT added
  // (the history replay appends after it, so it would land at the top of
  // the resumed transcript where nobody reads it).
  function onChatChanged(result) {
    const sessionId = String(result?.[AGENT_SESSION_ID] || '').trim();
    const discarded = queuedMessagesRef.current || [];
    chatSwitchPendingRef.current = false;
    prevTurnInFlightRef.current = false;
    if (discarded.length > 0) {
      commitQueue(() => []);
      // Also wipe the durable copies IMPERATIVELY: if the operator
      // switched task tabs while the POST was in flight, this component
      // is unmounted — the setState above no-ops and the persist effect
      // never runs, so the stale queue would resurrect on return and
      // auto-flush into the new chat (the exact bug being prevented).
      writeQueuedMessages(taskId, []);
      persistQueuedMessages(taskId, []);
      queuedMessagesRef.current = [];
      // Surface the dropped texts in a TOAST, not (only) a chat bubble:
      // on a switch, the history replay appends after local bubbles, so
      // a bubble lands at the top of the transcript where the auto-scroll
      // to bottom hides it.
      toast.show({
        kind: 'warning',
        title: `Discarded ${discarded.length} queued message(s)`,
        message: 'They were written for the previous chat:\n'
          + discarded.map((item) => `• ${item.text}`).join('\n'),
        durationMs: 12000,
      });
    }
    stream.resetChat();
    if (!sessionId) {
      stream.appendLocalEvent({
        source: ENTRY_SOURCE.LOCAL,
        kind: BUBBLE_KIND.SYSTEM,
        // Named from the ACTIVE backend, not hardcoded: this fires on a
        // backend switch too, and the Codex tab announcing a "fresh Claude
        // session" is exactly the confusion the tabs exist to remove.
        text: `🆕 new chat — your next message starts a fresh `
          + `${agentName} session. `
          + 'The previous conversation is in the chats menu.',
      });
    }
  }

  const hasVisible = useMemo(() => hasVisibleBubbles(stream.events), [stream.events]);
  const banner = lifecycleBanner(
    stream.lifecycle, taskId, hasVisible, agentName,
  );
  const composerDisabled = !canSend(stream.lifecycle, session);
  const composerHint = composerDisabledReason(stream.lifecycle, session);
  // Chat search state. Lifted here (not in EventLog) so the search
  // bar — which lives at the top of the chat area as a peer of
  // EventLog — and the highlight pass inside EventLog stay in sync
  // through a single source of truth. ``matchCount`` is reported
  // back by EventLog after its post-render DOM walk so the search
  // bar can show "X / N". ``currentMatchIndex`` is the navigation
  // cursor across that match run; EventLog scrolls and accents
  // whichever match is at this index.
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatchCount, setSearchMatchCount] = useState(0);
  const [searchCurrentIndex, setSearchCurrentIndex] = useState(0);
  // Reset the query (and the navigation cursor) when switching
  // tasks — a query that was open on task A shouldn't silently dim
  // task B's chat on tab switch.
  useEffect(() => {
    setSearchQuery('');
    setSearchCurrentIndex(0);
  }, [taskId]);
  // New query → reset cursor to first match. Clamp cursor if the
  // match count shrank from under it (e.g. a bubble was filtered
  // out by dedupe between renders).
  const handleSearchQueryChange = useCallback((next) => {
    setSearchQuery(next);
    setSearchCurrentIndex(0);
  }, []);
  const handleSearchMatchCount = useCallback((count) => {
    setSearchMatchCount(count);
    setSearchCurrentIndex((idx) => {
      if (count <= 0) { return 0; }
      if (idx >= count) { return count - 1; }
      return idx;
    });
  }, []);
  // Prev/next wrap around so the operator can step through without
  // hitting a "stuck at end" dead-state.
  const handlePrevMatch = useCallback(() => {
    setSearchCurrentIndex((idx) => {
      if (searchMatchCount <= 0) { return 0; }
      return (idx - 1 + searchMatchCount) % searchMatchCount;
    });
  }, [searchMatchCount]);
  const handleNextMatch = useCallback(() => {
    setSearchCurrentIndex((idx) => {
      if (searchMatchCount <= 0) { return 0; }
      return (idx + 1) % searchMatchCount;
    });
  }, [searchMatchCount]);
  const sessionHeader = (
    <SessionHeader
      session={session}
      needsAttention={needsAttention}
      onStopped={onStopped}
      onResume={onResume}
      onChatChanged={onChatChanged}
      onChatSwitchPending={onChatSwitchPending}
      streamLifecycle={stream.lifecycle}
      turnInFlight={stream.turnInFlight}
      awaitingBackground={stream.awaitingBackground}
      backgroundIsWorkflow={stream.backgroundIsWorkflow}
      onSendPrompt={onSendMessage}
      onWorkspaceMutated={onWorkspaceMutated}
      searchSlot={
        <ChatSearch
          query={searchQuery}
          onQueryChange={handleSearchQueryChange}
          matchCount={searchMatchCount}
          currentMatchIndex={searchCurrentIndex}
          onPrevMatch={handlePrevMatch}
          onNextMatch={handleNextMatch}
        />
      }
    />
  );
  return (
    <PanelCard {...panelProps}>
      <section id="session-detail">
        {headerSlot
          ? createPortal(sessionHeader, headerSlot)
          : sessionHeader}
        {/* One tab per agent, each owning its own chat history. Sits at the
            top of the chat pane (not in the task header) because it selects
            which CONVERSATION is below it — the same placement the editor
            extensions use. */}
        <AgentBackendTabs
          key={backendProbeNonce}
          taskId={taskId}
          activeBackend={String(session?.agent_backend || '')}
          onBackendChanged={onChatChanged}
          onChatChanged={onChatChanged}
          onChatSwitchPending={onChatSwitchPending}
          onSessionAdopted={onSessionAdopted}
          liveAgentSessionId={String(session?.[AGENT_SESSION_ID] || '')}
          onReadinessChange={setActiveBackendEntry}
          turnInFlight={stream.turnInFlight}
        />
        {/* The working indicator is the LAST entry inside the
            scrollable log, not a floating overlay. It scrolls with
            the messages and sits just after the newest one — so it
            reads as part of the chat and the transcript never bleeds
            through it (the earlier "floating dock" overlapped chat
            text that scrolled behind it). */}
        {/* The agent's permission ask lands HERE, portaled in by
            GlobalPermissionContainer (which stays the single owner of the
            submit/resolve path — see there). It is anchored above the
            composer, so its position does not depend on where it sits in
            this list.

            Rendered UNCONDITIONALLY, outside the ready/unready branch, the
            same way Layout keeps ``#task-header-slot`` alive. Inside the
            branch the node was destroyed and re-created whenever the
            operator clicked an agent tab whose CLI is not installed — and
            the container caches this node, so the portal went on writing
            into a DETACHED element: the ask vanished while the subprocess
            was still blocked waiting for the answer. An always-present slot
            has no such state to get stale. */}
        <div id="chat-permission-slot" />
        {backendUnready ? (
          <AgentBackendSetup
            backend={activeBackendEntry.id}
            error={activeBackendEntry.error}
            wired={activeBackendEntry.wired !== false}
            rechecking={rechecking}
            onRecheck={recheckBackends}
          />
        ) : (
        <>
        <EventLog
          taskId={taskId}
          agentName={backendLabel(activeBackend) || 'Agent'}
          entries={stream.events}
          banner={banner}
          searchQuery={searchQuery}
          searchCurrentIndex={searchCurrentIndex}
          onSearchMatchCount={handleSearchMatchCount}
          onOpenFile={onOpenFile}
          pinRequestId={pinRequestId}
          footer={
            <WorkingIndicator
              active={stream.turnInFlight || hasPendingPermission}
              waitingForApproval={hasPendingPermission}
              lastEventAt={stream.lastEventAt}
              onContinue={() => deliverMessage('continue')}
            />
          }
        />
        <QueuedMessageList
          items={queuedMessages}
          onSteer={steerQueuedMessage}
          onRemove={removeQueuedMessage}
          onEdit={editQueuedMessage}
        />
        <MessageForm
          ref={composerRef}
          taskId={taskId}
          agentBackend={activeBackend}
          turnInFlight={stream.turnInFlight}
          onSubmit={onSendMessage}
          disabled={composerDisabled}
          disabledReason={composerHint}
          availableModels={availableModels}
          selectedModel={selectedModel}
          onModelChange={handleModelChange}
          effortLevels={effortLevels}
          selectedEffort={selectedEffort}
          effortDefault={effortDefault}
          onEffortChange={handleEffortChange}
          agentMode={agentMode}
          onAgentModeChange={handleAgentModeChange}
          remoteControl={remoteControl}
          onRemoteControlChange={handleRemoteControlChange}
          contextUsage={contextUsage}
          onStop={onComposerStop}
          planAvailable={planAvailable}
          onOpenPlan={onOpenPlan}
        />
        </>
        )}
      </section>
    </PanelCard>
  );
}

// Monotonic id for queued messages — used as the stable React key
// on the floating <QueuedMessageList> rows. Date.now() alone isn't
// enough: rapid Enter-Enter-Enter would mint duplicates within the
// same ms.
let _queuedIdCounter = 0;
function _newQueuedId() {
  _queuedIdCounter += 1;
  return `q-${Date.now()}-${_queuedIdCounter}`;
}


function canSend(lifecycle, session) {
  // Only block when the server has no record at all. CLOSED/IDLE still
  // accept sends — the backend respawns Claude on demand, and after a
  // rate-limit hit the operator needs to be able to retry once the
  // window resets without manually refreshing.
  if (lifecycle === SESSION_LIFECYCLE.MISSING) { return false; }
  return true;
}

function composerDisabledReason(lifecycle, session) {
  if (canSend(lifecycle, session)) { return ''; }
  return 'No record for this task on the server.';
}

// Banner is the always-visible status line at the top of the log.
// - CONNECTING / IDLE / MISSING / CLOSED → always show the explanatory text.
// - STREAMING → show "Connected, waiting…" *only* until at least one
//   bubble appears, then suppress so the chat reads cleanly.
// Exported for unit tests. Pure function with no React deps.
export function lifecycleBanner(
  lifecycle, taskId, hasVisible, agentName = 'the agent',
) {
  switch (lifecycle) {
    case SESSION_LIFECYCLE.CONNECTING:
      return `Connecting to session for ${taskId}…`;
    case SESSION_LIFECYCLE.STREAMING:
      return hasVisible
        ? null
        : `Connected — waiting for ${agentName}'s first reply…`;
    case SESSION_LIFECYCLE.IDLE:
      return '(no live subprocess for this tab — chat will resume when kato re-spawns it)';
    case SESSION_LIFECYCLE.MISSING:
      return 'No record for this task on the server.';
    case SESSION_LIFECYCLE.CLOSED:
      return '(session ended)';
    default:
      return null;
  }
}

// True when at least one entry would produce a visible bubble. Used by
// the banner so we don't show "waiting for first reply" once chat
// content actually arrives. Mirrors EventLog's filtering rules.
// Exported for unit tests. Pure function with no React deps.
export function hasVisibleBubbles(entries) {
  return entries.some((entry) => {
    if (entry?.source === ENTRY_SOURCE.LOCAL) { return true; }
    if (entry?.source === ENTRY_SOURCE.HISTORY) { return true; }
    const type = entry?.raw?.type;
    if (!type) { return false; }
    if (type === CLAUDE_EVENT.USER || type === CLAUDE_EVENT.STREAM_EVENT) { return false; }
    if (type === CLAUDE_EVENT.PERMISSION_REQUEST
        || type === CLAUDE_EVENT.CONTROL_REQUEST
        || type === CLAUDE_EVENT.PERMISSION_RESPONSE) { return false; }
    if (type === CLAUDE_EVENT.SYSTEM && entry.raw.subtype !== CLAUDE_SYSTEM_SUBTYPE.INIT) {
      return false;
    }
    if (type === CLAUDE_EVENT.ASSISTANT) {
      const content = entry.raw?.message?.content || [];
      return content.some(
        (b) => (b?.type === 'text' && b.text) || b?.type === 'tool_use',
      );
    }
    return true;
  });
}
