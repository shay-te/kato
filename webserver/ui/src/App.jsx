import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AdoptTaskModal from './components/AdoptTaskModal.jsx';
import DiffPane from './components/DiffPane.jsx';
import EditorPane from './components/EditorPane.jsx';
import ForgetTaskModal from './components/ForgetTaskModal.jsx';
import GlobalPermissionContainer from './components/GlobalPermissionContainer.jsx';
import Header from './components/Header.jsx';
import Layout from './components/Layout.jsx';
import OrchestratorActivityFeed from './components/OrchestratorActivityFeed.jsx';
import PlanPane from './components/PlanPane.jsx';
import RightPane from './components/RightPane.jsx';
import FileTabStrip from './components/FileTabStrip.jsx';
import SafetyBanner from './components/SafetyBanner.jsx';
import SetupModeGate from './components/SetupModeGate.jsx';
import AgentVersionBanner from './components/AgentVersionBanner.jsx';
import SessionDetail from './components/SessionDetail.jsx';
import SettingsDrawer from './components/SettingsDrawer.jsx';
import TabList from './components/TabList.jsx';
import ToastContainer from './components/ToastContainer.jsx';
import { forgetTaskWorkspace, triggerScan } from './api.js';
import { toast } from './stores/toastStore.js';
import { ChatComposerContext } from './contexts/ChatComposerContext.jsx';
import { useNotifications } from './hooks/useNotifications.js';
import { useNotificationRouting } from './hooks/useNotificationRouting.js';
import { useResizable } from './hooks/useResizable.js';
import { useSafetyState } from './hooks/useSafetyState.js';
import { useConfigStatus } from './hooks/useConfigStatus.js';
import { refreshAgentVersion } from './hooks/useAgentVersion.js';
import { refreshCatalogs } from './hooks/useCatalogRefresh.js';
import { useSessions } from './hooks/useSessions.js';
import { clearTaskStreamCache } from './hooks/useSessionStream.js';
import {
  setActiveTask as activateTaskCache,
  forgetTask as forgetTaskCache,
  registerOnEvict,
} from './stores/taskCache/index.js';
import { forgetQueuedMessages } from './utils/queuedMessagesStore.js';
import { clearImageDraft } from './utils/composerImageDraft.js';
import { clearFileContentCacheForTask } from './utils/fileContentCache.js';
import { useStatusFeed } from './hooks/useStatusFeed.js';
import { useTaskAttention } from './hooks/useTaskAttention.js';
import { useTaskTabShortcuts } from './hooks/useTaskTabShortcuts.js';
import { useRememberedToolDecisions } from './hooks/useRememberedToolDecisions.js';
import { usePlanWatch } from './hooks/usePlanWatch.js';
import { CLAUDE_EVENT } from './constants/claudeEvent.js';
import { agentStatusStore } from './stores/agentStatusStore.js';
import { mergePendingPermissionTaskIds } from './utils/sessionAttention.js';
import { closeTab, patchTab, upsertTab } from './utils/fileTabs.js';

const RIGHT_PANE_DEFAULT_WIDTH = 380;
const RIGHT_PANE_MIN_WIDTH = 220;
const RIGHT_PANE_MAX_WIDTH = 1400;
const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';
// The centre file/diff preview must stay usable no matter how wide the chat
// is dragged: the chat's max width is capped at (viewport − this), so the
// centre always keeps at least this many px and the LEFT tree collapses to
// fill the gap instead. MUST match the centre column's min-width in app.scss
// (#layout.has-top-tabs grid-template-columns).
const CENTER_PANE_MIN_WIDTH = 360;
const LEFT_PANE_DEFAULT_WIDTH = 320;
const LEFT_PANE_MIN_WIDTH = 220;
// Generous upper bound — operators routinely widen the Files /
// Changes pane to read long diffs side-by-side with the chat, and
// 700px capped that too early. The grid uses minmax(0, …) so the
// centre/right columns still collapse gracefully at large values.
const LEFT_PANE_MAX_WIDTH = 1200;
const LEFT_PANE_STORAGE_KEY = 'kato.leftPaneWidth';

// Govern the satellite per-task caches by the task cache's LRU: when a task is
// evicted (fell out of the last-N viewed) OR forgotten, purge its chat-stream
// and file-content caches too — so ONE eviction authority bounds every
// per-task cache to the same retained set. Registered once at module load.
registerOnEvict(clearTaskStreamCache);
registerOnEvict(clearFileContentCacheForTask);

export default function App() {
  const [activeTaskId, setActiveTaskIdState] = useState('');
  // The chat-composer textarea owns its own value (see
  // ``MessageForm`` for why — it's the per-keystroke perf fix).
  // App talks to it via ``composerRef.current.appendFragment(...)``
  // so file-tree clicks / Cmd+P picks / diff right-click can push
  // text into the composer without re-rendering the whole tree
  // on every keystroke.
  const composerRef = useRef(null);
  const { sessions, refresh } = useSessions();
  const attention = useTaskAttention();
  // Live agent status published by the active task's SessionDetail. Subscribed
  // once here and passed down (via TabList) so the tab dot/badge derive from the
  // same value as the header chip (UNA-2492).
  const [agentStatuses, setAgentStatuses] = useState({});
  useEffect(() => agentStatusStore.subscribe(setAgentStatuses), []);
  // Remembered "Allow always"/"Deny always" decisions are backend-owned
  // (kato_core_lib/helpers/tool_decision_store.py) — the server
  // auto-resolves a matching pending ask before it ever reaches the
  // tab-attention feed, the permission modal, or the per-task SSE
  // stream. This read-only cache of the backend's decisions is only
  // needed for the status-feed notification de-dup hint (see
  // useNotificationRouting) — a log line that fires before that
  // server-side check runs.
  const rememberedToolDecisions = useRememberedToolDecisions();
  // "+ Add task" picker open/closed state — owned by App so the
  // modal sits above the layout (not inside TabList) and can fire
  // a ``refresh()`` of the session list once an adoption succeeds.
  const [addTaskModalOpen, setAddTaskModalOpen] = useState(false);
  const [workspaceVersion, setWorkspaceVersion] = useState(() => ({}));
  // Tracks whether the operator has manually picked a tab. We auto-focus
  // the live task on the *first* event arrival, but only when the operator
  // hasn't expressed a preference — never steal focus mid-investigation.
  const userPickedTabRef = useRef(false);

  // Debounce per-task workspace bumps so a burst of tool_results during a
  // single turn doesn't make Files / Changes blink every 200ms. The
  // refetch happens 1.2s after the last bump request.
  const bumpTimersRef = useRef({});
  useEffect(() => {
    return () => {
      for (const handle of Object.values(bumpTimersRef.current)) {
        window.clearTimeout(handle);
      }
    };
  }, []);
  const bumpWorkspaceVersion = useCallback((taskId) => {
    if (!taskId) { return; }
    const existing = bumpTimersRef.current[taskId];
    if (existing) { window.clearTimeout(existing); }
    bumpTimersRef.current[taskId] = window.setTimeout(() => {
      delete bumpTimersRef.current[taskId];
      setWorkspaceVersion((prev) => ({
        ...prev,
        [taskId]: (prev[taskId] || 0) + 1,
      }));
    }, 1200);
  }, []);

  // Tab switch → MessageForm remounts (it's keyed on
  // ``activeTaskId`` via SessionDetail), so its internal composer
  // state resets without us doing anything here. The old
  // ``setComposerValue('')`` on taskId change is no longer needed.

  const appendToInput = useCallback((fragment) => {
    const composer = composerRef.current;
    if (composer && typeof composer.appendFragment === 'function') {
      composer.appendFragment(fragment);
    }
  }, []);

  // Reconnect the active task's SSE stream when a diff comment
  // immediately triggers a Claude spawn (so the operator sees
  // Claude working without having to click into the chat pane).
  const sessionReconnectRef = useRef(null);
  const handleRegisterReconnect = useCallback((fn) => {
    sessionReconnectRef.current = fn;
  }, []);
  const handleCommentSpawned = useCallback(() => {
    sessionReconnectRef.current?.();
  }, []);
  const handleDiffCommentsChanged = useCallback(() => {
    bumpWorkspaceVersion(activeTaskId);
  }, [activeTaskId, bumpWorkspaceVersion]);

  // The git buttons in the session header (Merge master / Pull) mutate
  // the workspace clone on disk OUTSIDE the agent stream, so the normal
  // tool-result bump never fires. Bump explicitly so the Changes tab /
  // Files tree / open editor refetch the post-merge content.
  const handleWorkspaceMutated = useCallback((taskId) => {
    bumpWorkspaceVersion(taskId || activeTaskId);
  }, [activeTaskId, bumpWorkspaceVersion]);

  const setActiveTaskId = useCallback((taskId) => {
    userPickedTabRef.current = true;
    setActiveTaskIdState(taskId);
    attention.clear(taskId);
    // Force a polled /api/sessions fetch on every tab switch. The
    // OUTGOING tab's SessionDetail unmounts immediately and clears its
    // live ``agentStatusStore`` entry, so its dot falls back to the
    // polled ``session.working`` field — which is otherwise refreshed
    // only every 5s. Without an on-switch refresh the dot transiently
    // reads "idle" for the working background tab even though Claude
    // is still mid-turn there (operator report: "moving to the task
    // tab shows it's working; switching to another tab shows it idle
    // again"). The poll is cheap and only runs on an actual click.
    refresh();
  }, [attention, refresh]);

  // Drive the per-task view-data cache: whenever the viewed task changes,
  // make it active so the cache retains the last few tasks' data (tree /
  // diff / comments / …) in memory and revalidates in the background —
  // switching back is then instant instead of a full refetch. Every
  // activation path funnels through ``activeTaskId`` state, so one effect
  // covers them all (tab click, keyboard, adopt, auto-focus).
  useEffect(() => {
    if (activeTaskId) { activateTaskCache(activeTaskId); }
  }, [activeTaskId]);

  // Tab / Shift+Tab step through the task strip at the top (guards
  // against text fields + open dialogs so normal focus tabbing still
  // works there).
  useTaskTabShortcuts({ sessions, activeTaskId, onSelect: setActiveTaskId });

  // The tab "X" no longer forgets immediately — it stages the task
  // for a hard-confirm modal. Forgetting wipes the local clone and
  // is irreversible, so the operator must approve it in
  // ForgetTaskModal before anything is deleted.
  const [forgetCandidate, setForgetCandidate] = useState(null);
  const requestForgetTask = useCallback((taskId) => {
    if (!taskId) { return; }
    const session = sessions.find((s) => s.task_id === taskId)
      || { task_id: taskId };
    setForgetCandidate(session);
  }, [sessions]);
  const cancelForgetTask = useCallback(() => {
    setForgetCandidate(null);
  }, []);

  // Open files for the middle pane, VS Code-tab style: every file the
  // operator opens gets its OWN tab (never replaces another tab's
  // content) via utils/fileTabs.js. Lifted to App so FilesTab
  // (rendered on the left) and EditorPane/DiffPane (rendered in the
  // centre) can talk through a single source of truth without
  // coupling them directly. The full tab list + which one is active
  // is remembered per task so switching tasks restores the operator's
  // whole set of open tabs instead of dropping the centre pane back
  // to empty.
  const [openTabs, setOpenTabs] = useState([]);
  const [activeTabKey, setActiveTabKey] = useState(null);
  const [planOpen, setPlanOpen] = useState(false);
  const [fileTreeFocusTarget, setFileTreeFocusTarget] = useState(null);
  const openTabsRef = useRef([]);
  const activeTabKeyRef = useRef(null);
  // Per task: { tabs, activeKey }. Restored wholesale on task switch.
  const fileViewByTaskRef = useRef({});
  const openFileRequestRef = useRef(0);
  const fileTreeFocusRequestRef = useRef(0);

  const doForgetTask = useCallback(async (taskId) => {
    if (!taskId) { return; }
    // Show the operator what's happening — silently failing was the
    // original bug. Backend now returns 500 with a concrete error
    // message when the workspace dir can't be removed (live process
    // file locks, antivirus, etc.).
    const result = await forgetTaskWorkspace(taskId);
    if (!result.ok) {
      // Route the error text through the canonical apiErrorMessage
      // precedence (body.error → result.error → fallback) instead of
      // the previous hand-rolled chain, so this site agrees with every
      // other error toast on which message wins.
      toast.errorFromResult(result, {
        title: `Couldn't forget ${taskId}`,
        fallback: 'unknown error — see kato logs for details',
        durationMs: 12000,
      });
      // Refresh anyway so the operator sees the current state — a
      // partial cleanup (session record gone, workspace dir still
      // there) should still cause the tab to flicker its dot.
      refresh();
      return;
    }
    // Forget the task in the shared view-data cache: purges its store slices
    // (tree / diff / comments / publish / PR) AND fans out to the registered
    // satellite purgers (chat-stream + file-content) — one call clears every
    // per-task cache. A later re-adopt reuses the same id but a FRESH clone,
    // so none of it may survive.
    forgetTaskCache(taskId);
    // Also drop this task's durable composer drafts so a forgotten task leaves
    // no orphaned base64 image data behind in IndexedDB. Best-effort.
    forgetQueuedMessages(taskId);
    clearImageDraft(taskId);
    if (activeTaskId === taskId) {
      setActiveTaskIdState('');
      userPickedTabRef.current = false;
    }
    delete fileViewByTaskRef.current[taskId];
    if (openTabsRef.current.some((tab) => tab.taskId === taskId)) {
      openTabsRef.current = [];
      activeTabKeyRef.current = null;
      setOpenTabs([]);
      setActiveTabKey(null);
    }
    refresh();
    toast.show({
      kind: 'success',
      title: `Forgot ${taskId}`,
      message: 'Workspace clone and Claude session removed.',
    });
  }, [activeTaskId, refresh]);

  const confirmForgetTask = useCallback(() => {
    const taskId = forgetCandidate?.task_id;
    setForgetCandidate(null);
    if (taskId) { doForgetTask(taskId); }
  }, [forgetCandidate, doForgetTask]);

  const [scanPending, setScanPending] = useState(false);
  const handleScanNow = useCallback(async () => {
    setScanPending(true);
    await triggerScan();
    await refresh();
    setScanPending(false);
  }, [refresh]);

  // Header Refresh: re-scan tickets + reload sessions AND re-probe the agent
  // CLI version + picker catalogues, so a CLI upgrade or settings change shows
  // in the banner / model picker with no kato restart.
  const handleHeaderRefresh = useCallback(async () => {
    refreshAgentVersion();
    refreshCatalogs();
    await refresh();
  }, [refresh]);

  const onTaskClickFromNotification = useCallback((taskId) => {
    setActiveTaskId(taskId);
  }, [setActiveTaskId]);
  const notifications = useNotifications({
    activeTaskId,
    onTaskClick: onTaskClickFromNotification,
  });

  const routing = useNotificationRouting(notifications.notify, {
    recallToolDecision: rememberedToolDecisions.recall,
    activeTaskId,
  });

  const handleStatusEntry = useCallback((entry) => {
    routing.onStatusEntry(entry);
  }, [routing]);

  const handlePendingPermissionChange = useCallback((taskId, pending) => {
    if (!taskId) { return; }
    if (pending) {
      attention.mark(taskId);
      return;
    }
    attention.clear(taskId);
  }, [attention]);

  const handleSessionEvent = useCallback((raw, taskId) => {
    routing.onSessionEvent(raw, taskId);
    if (!raw?.type || !taskId) { return; }
    if (raw.type === CLAUDE_EVENT.PERMISSION_REQUEST
        || raw.type === CLAUDE_EVENT.CONTROL_REQUEST) {
      // The webserver already auto-resolves a pending request against a
      // remembered decision before it's ever published over SSE (see
      // _maybe_auto_resolve_live_event in kato_webserver/app.py) — so
      // reaching here always means a real ask that needs the operator.
      attention.mark(taskId);
    } else if (raw.type === CLAUDE_EVENT.PERMISSION_RESPONSE
        || raw.type === CLAUDE_EVENT.RESULT) {
      attention.clear(taskId);
    }
    // Keep the right pane in sync with disk: bump on every tool result
    // (USER messages carrying tool_result payloads) and on turn end so
    // Files + Changes refetch as soon as the agent has touched anything.
    if (raw.type === CLAUDE_EVENT.USER || raw.type === CLAUDE_EVENT.RESULT) {
      bumpWorkspaceVersion(taskId);
    }
    // RESULT also implies the task may have transitioned state on the
    // ticket platform — refresh the session list now instead of waiting
    // up to REFRESH_INTERVAL_MS for the next poll tick.
    if (raw.type === CLAUDE_EVENT.RESULT) {
      refresh();
    }
    // Auto-focus the live task tab when kato starts working — but only if
    // the operator hasn't manually picked a tab yet. Triggered by ASSISTANT
    // events (the agent saying or doing something) rather than history
    // replay or status pings, so we follow real activity, not boot noise.
    if (raw.type === CLAUDE_EVENT.ASSISTANT
        && !userPickedTabRef.current
        && taskId !== activeTaskId) {
      setActiveTaskIdState(taskId);
    }
  }, [routing, attention, bumpWorkspaceVersion, refresh, activeTaskId]);

  const status = useStatusFeed(handleStatusEntry);
  const safetyState = useSafetyState();
  // First-run gate: when kato booted unconfigured, SetupModeGate overlays the
  // whole app with the setup wizard. ``refreshConfigStatus`` lets the wizard
  // re-check the moment it saves a setting.
  const { status: configStatus, refresh: refreshConfigStatus } = useConfigStatus();

  // Track viewport width so the chat's max can leave the centre pane its
  // minimum at any window size — a static cap squeezed the centre on
  // narrower screens.
  const [viewportWidth, setViewportWidth] = useState(
    () => (typeof window !== 'undefined' ? window.innerWidth : 1920),
  );
  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const resizer = useResizable({
    storageKey: RIGHT_PANE_STORAGE_KEY,
    defaultWidth: RIGHT_PANE_DEFAULT_WIDTH,
    minWidth: RIGHT_PANE_MIN_WIDTH,
    // Cap so the centre pane always keeps CENTER_PANE_MIN_WIDTH: dragging the
    // chat wider collapses the LEFT tree, never squeezes the centre below
    // usable. Still bounded by the absolute ceiling.
    maxWidth: Math.max(
      RIGHT_PANE_MIN_WIDTH,
      Math.min(RIGHT_PANE_MAX_WIDTH, viewportWidth - CENTER_PANE_MIN_WIDTH),
    ),
    anchor: 'right',
  });
  const leftResizer = useResizable({
    storageKey: LEFT_PANE_STORAGE_KEY,
    defaultWidth: LEFT_PANE_DEFAULT_WIDTH,
    minWidth: LEFT_PANE_MIN_WIDTH,
    maxWidth: LEFT_PANE_MAX_WIDTH,
    anchor: 'left',
  });
  // Operator clicks the "Scanning for…" pill at the top → the
  // centre column (normally the read-only Monaco editor) gets
  // swapped for the live orchestrator activity feed. Clicking
  // again toggles it back. Lives at App so the pill button and
  // the centre cell stay in sync without prop-drilling through
  // every intermediate.
  const [orchestratorOpen, setOrchestratorOpen] = useState(false);
  const toggleOrchestrator = useCallback(() => {
    setOrchestratorOpen((open) => !open);
  }, []);
  // Settings drawer state. Lives at App so the gear button in the
  // Header and the drawer rendered next to the layout share a
  // single boolean — no prop-drilling, no context.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const activeSession = sessions.find((s) => s.task_id === activeTaskId) || null;
  const attentionTaskIds = useMemo(() => {
    return mergePendingPermissionTaskIds(attention.taskIds, sessions);
  }, [attention.taskIds, sessions]);
  const activeNeedsAttention = !!activeTaskId && attentionTaskIds.has(activeTaskId);
  const activeSessionKey = activeTaskId || '__none__';
  const activeWorkspaceVersion = workspaceVersion[activeTaskId] || 0;
  useEffect(() => {
    const remembered = activeTaskId
      ? fileViewByTaskRef.current[activeTaskId] || null
      : null;
    const tabs = remembered
      ? remembered.tabs.map((tab) => ({ ...tab, restoreViewState: true }))
      : [];
    const restoredActiveKey = remembered ? remembered.activeKey : null;
    openTabsRef.current = tabs;
    activeTabKeyRef.current = restoredActiveKey;
    setOpenTabs(tabs);
    setActiveTabKey(restoredActiveKey);
    setPlanOpen(false);
    setFileTreeFocusTarget(null);
  }, [activeTaskId]);
  function rememberTabsView(taskId, tabs, activeKey) {
    if (!taskId) { return; }
    fileViewByTaskRef.current[taskId] = { tabs, activeKey };
  }
  const handleOpenFile = useCallback((info) => {
    // ``info`` shape from FilesTab: { absolutePath, relativePath, repoId }.
    // ``repoId`` is required for the comments POST (the backend keys
    // comments by repo + relative path so a comment on
    // ``src/auth.py`` in repo A doesn't collide with the same path
    // in repo B).
    if (!info || !info.absolutePath) {
      if (activeTaskId) {
        delete fileViewByTaskRef.current[activeTaskId];
      }
      openTabsRef.current = [];
      activeTabKeyRef.current = null;
      setOpenTabs([]);
      setActiveTabKey(null);
      return;
    }
    // Opening a file must take over the centre column. If the
    // operator had the orchestrator-activity feed (or the plan) open,
    // close it so the file actually shows instead of staying hidden
    // behind it.
    setOrchestratorOpen(false);
    setPlanOpen(false);
    openFileRequestRef.current += 1;
    // Every open either focuses an ALREADY-open tab for this repo+path
    // (same file, possibly toggling file<->diff view) or appends a
    // brand new tab right after the current one — it never replaces a
    // DIFFERENT file's tab (VS Code-style multi-file tabs).
    const { tabs, activeKey } = upsertTab(
      openTabsRef.current,
      activeTabKeyRef.current,
      { ...info, openRequestId: openFileRequestRef.current },
      activeTaskId,
    );
    openTabsRef.current = tabs;
    activeTabKeyRef.current = activeKey;
    rememberTabsView(activeTaskId, tabs, activeKey);
    setOpenTabs(tabs);
    setActiveTabKey(activeKey);
  }, [activeTaskId]);
  // Switch which already-open tab is active (file-tab-strip click).
  // Flushes openTabsRef into state first: view-state patches
  // (handleFileViewStateChange, below) only touch the ref between
  // renders, so a tab switch is the point where the ref's latest
  // scroll/cursor position needs to actually become visible state.
  const handleSelectFileTab = useCallback((key) => {
    setOrchestratorOpen(false);
    setPlanOpen(false);
    activeTabKeyRef.current = key;
    rememberTabsView(activeTaskId, openTabsRef.current, key);
    setOpenTabs(openTabsRef.current);
    setActiveTabKey(key);
  }, [activeTaskId]);
  const handleCloseFileTab = useCallback((key) => {
    const { tabs, activeKey } = closeTab(openTabsRef.current, activeTabKeyRef.current, key);
    openTabsRef.current = tabs;
    activeTabKeyRef.current = activeKey;
    rememberTabsView(activeTaskId, tabs, activeKey);
    setOpenTabs(tabs);
    setActiveTabKey(activeKey);
  }, [activeTaskId]);
  // Open the agent's plan in the centre pane, replacing whatever file/diff
  // tab was showing (the tab strip + its tabs stay intact underneath — this
  // only sets a display flag, it doesn't touch openTabs at all). Deliberately
  // NOT persisted — the plan view is ephemeral, so a reload / task switch
  // won't re-yank the centre pane onto a plan the operator already dismissed.
  const handleOpenPlan = useCallback(() => {
    if (!activeTaskId) { return; }
    setOrchestratorOpen(false);
    setPlanOpen(true);
  }, [activeTaskId]);
  const { content: planContent, available: planAvailable } = usePlanWatch(
    activeTaskId, handleOpenPlan,
  );
  const handleFileViewStateChange = useCallback((viewState) => {
    const key = activeTabKeyRef.current;
    if (!key || !viewState) { return; }
    // Ref-only update (no setOpenTabs) — this fires on every Monaco
    // scroll/cursor tick, so triggering a re-render here would be
    // wasteful. The value is only ever read back on tab-switch or
    // task-switch restore (both of which read the ref / the
    // remembered-per-task map fresh), never directly from render state.
    const tabs = patchTab(openTabsRef.current, key, viewState);
    openTabsRef.current = tabs;
    rememberTabsView(activeTaskId, tabs, key);
  }, [activeTaskId]);
  const handleFocusFileInTree = useCallback((target) => {
    const relativePath = String(target?.relativePath || target?.absolutePath || '').trim();
    if (!relativePath) { return; }
    fileTreeFocusRequestRef.current += 1;
    setFileTreeFocusTarget({
      repoId: String(target?.repoId || '').trim(),
      relativePath,
      requestId: fileTreeFocusRequestRef.current,
    });
  }, []);
  // Memoize so the context value is reference-stable across App
  // renders. Without this, EVERY ``useChatComposer()`` consumer
  // (FilesTab, ChangesTab via DiffFileWithComments, etc.)
  // re-renders on every App render — including the wasteful ones
  // that fire on tab focus changes / poll ticks.
  const composerContextValue = useMemo(() => ({ appendToInput }), [appendToInput]);
  // Guards against a stale previous task's tabs flashing into view during
  // the one render between activeTaskId changing and the restore effect
  // (above) committing the new task's tabs.
  const tabsForActiveTask = useMemo(
    () => openTabs.filter((tab) => tab.taskId === activeTaskId),
    [openTabs, activeTaskId],
  );
  const activeOpenFile = tabsForActiveTask.find((tab) => tab.key === activeTabKey) || null;
  let centerPane;
  if (orchestratorOpen) {
    centerPane = (
      <OrchestratorActivityFeed
        history={status.history}
        onClose={toggleOrchestrator}
      />
    );
  } else if (planOpen) {
    centerPane = <PlanPane content={planContent} />;
  } else {
    const filePane = activeOpenFile?.view === 'diff' ? (
      <DiffPane
        openFile={activeOpenFile}
        workspaceVersion={activeWorkspaceVersion}
        onCommentSpawned={handleCommentSpawned}
        onFocusFileInTree={handleFocusFileInTree}
        onCommentsChanged={handleDiffCommentsChanged}
        onViewStateChange={handleFileViewStateChange}
        onOpenFile={handleOpenFile}
      />
    ) : (
      <EditorPane
        openFile={activeOpenFile}
        onCommentSpawned={handleCommentSpawned}
        onViewStateChange={handleFileViewStateChange}
        onOpenFile={handleOpenFile}
      />
    );
    centerPane = (
      <div className="center-pane-with-tabs">
        <FileTabStrip
          tabs={tabsForActiveTask}
          activeKey={activeTabKey}
          onSelect={handleSelectFileTab}
          onClose={handleCloseFileTab}
        />
        {filePane}
      </div>
    );
  }
  const layout = (
    <Layout
      rightWidth={resizer.width}
      leftWidth={leftResizer.width}
      top={
        <TabList
          sessions={sessions}
          activeTaskId={activeTaskId}
          attentionTaskIds={attentionTaskIds}
          agentStatuses={agentStatuses}
          onSelect={setActiveTaskId}
          onForget={requestForgetTask}
          onOpenAddTask={() => setAddTaskModalOpen(true)}
          onScanNow={handleScanNow}
          scanPending={scanPending}
        />
      }
      // New 3-column layout, left → right:
      //   left   Files + Changes tree (fixed-width column)
      //   center Monaco read-only editor (driven by openFile)
      //   right  Chat session (resizable via the existing resizer)
      //
      // ``width`` + ``onResizePointerDown`` deliberately omitted
      // on the files pane — that pair used to size the pane via an
      // inline ``style={{ width }}``, which now lives in the LEFT
      // grid cell whose track is fixed. Leaving it in would let
      // the pane bleed past its cell into the editor column.
      // The resizer keeps driving ``--right-pane-width`` for the
      // chat column on the right.
      left={
        <RightPane
          activeTaskId={activeTaskId}
          activeTaskSummary={activeSession?.task_summary || ''}
          workspaceVersion={activeWorkspaceVersion}
          focusFileTarget={fileTreeFocusTarget}
          openFile={activeOpenFile}
          onOpenFile={handleOpenFile}
          onResizePointerDown={leftResizer.onPointerDown}
        />
      }
      center={centerPane}
      right={
        <SessionDetail
          key={activeSessionKey}
          session={activeSession}
          needsAttention={activeNeedsAttention}
          onActivity={handleSessionEvent}
          onPendingPermissionChange={handlePendingPermissionChange}
          composerRef={composerRef}
          onResizePointerDown={resizer.onPointerDown}
          onOpenFile={handleOpenFile}
          onRegisterReconnect={handleRegisterReconnect}
          onWorkspaceMutated={handleWorkspaceMutated}
          planAvailable={planAvailable}
          onOpenPlan={handleOpenPlan}
        />
      }
    />
  );

  return (
    <>
      <ToastContainer />
      {/* ``hidden`` while the Settings drawer is open: the gate's overlay
          (z-index 1500) would otherwise paint OVER the drawer (1000) and
          dead-end the wizard's "Open full settings" path. Kept mounted (not
          unmounted) so the wizard's typed state survives the round-trip. */}
      <SetupModeGate
        status={configStatus}
        hidden={settingsOpen}
        onRefreshStatus={refreshConfigStatus}
        onOpenFullSettings={openSettings}
      />
      <SafetyBanner state={safetyState} />
      <AgentVersionBanner />
      <Header
        onRefresh={handleHeaderRefresh}
        statusLatest={status.latest}
        statusStale={status.stale}
        statusConnected={status.connected}
        onStatusClick={toggleOrchestrator}
        statusActive={orchestratorOpen}
        onOpenSettings={openSettings}
      />
      <SettingsDrawer
        open={settingsOpen}
        onClose={closeSettings}
        notificationProps={{
          enabled: notifications.enabled,
          supported: notifications.supported,
          permission: notifications.permission,
          kindPrefs: notifications.kindPrefs || {},
          onSetKindEnabled: notifications.setKindEnabled,
          onToggle: notifications.toggle,
        }}
      />
      <ChatComposerContext.Provider value={composerContextValue}>
        {layout}
      </ChatComposerContext.Provider>
      {/* The SINGLE permission-approval modal for EVERY task, driven by the
          shared permissionStore (authoritative poll + the focused task's
          live SSE). Surfaces the dialog no matter which task is in view and
          without a page refresh. */}
      <GlobalPermissionContainer />
      {forgetCandidate && (
        <ForgetTaskModal
          session={forgetCandidate}
          onConfirm={confirmForgetTask}
          onCancel={cancelForgetTask}
        />
      )}
      {addTaskModalOpen && (
        <AdoptTaskModal
          alreadyAdoptedIds={new Set(sessions.map((s) => s.task_id))}
          onClose={() => setAddTaskModalOpen(false)}
          onAdopted={(body) => {
            // Refresh the session list so the adopted task's tab
            // appears, then auto-select it so the operator lands
            // on the new chat without an extra click.
            refresh();
            const adoptedId = String(body?.task_id || '').trim();
            if (adoptedId) { setActiveTaskId(adoptedId); }
          }}
        />
      )}
    </>
  );
}
