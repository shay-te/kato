import { useCallback, useEffect, useRef, useState } from 'react';
import Header from './components/Header.jsx';
import Layout from './components/Layout.jsx';
import RightPane from './components/RightPane.jsx';
import SafetyBanner from './components/SafetyBanner.jsx';
import SessionDetail from './components/SessionDetail.jsx';
import StatusBar from './components/StatusBar.jsx';
import TabList from './components/TabList.jsx';
import ToastContainer from './components/ToastContainer.jsx';
import { forgetTaskWorkspace } from './api.js';
import { useNotifications } from './hooks/useNotifications.js';
import { useNotificationRouting } from './hooks/useNotificationRouting.js';
import { useResizable } from './hooks/useResizable.js';
import { useSafetyState } from './hooks/useSafetyState.js';
import { useSessions } from './hooks/useSessions.js';
import { useStatusFeed } from './hooks/useStatusFeed.js';
import { useTaskAttention } from './hooks/useTaskAttention.js';
import { classifyStatusEntry } from './utils/classifyStatusEntry.js';
import { CLAUDE_EVENT } from './constants/claudeEvent.js';
import { NOTIFICATION_KIND } from './constants/notificationKind.js';

const RIGHT_PANE_DEFAULT_WIDTH = 380;
const RIGHT_PANE_MIN_WIDTH = 220;
const RIGHT_PANE_MAX_WIDTH = 900;
const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';

export default function App() {
  const [activeTaskId, setActiveTaskIdState] = useState('');
  const { sessions, refresh } = useSessions();
  const attention = useTaskAttention();
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

  const setActiveTaskId = useCallback((taskId) => {
    userPickedTabRef.current = true;
    setActiveTaskIdState(taskId);
    attention.clear(taskId);
  }, [attention]);

  const handleForgetTask = useCallback(async (taskId) => {
    if (!taskId) { return; }
    await forgetTaskWorkspace(taskId);
    if (activeTaskId === taskId) {
      setActiveTaskIdState('');
      userPickedTabRef.current = false;
    }
    refresh();
  }, [activeTaskId, refresh]);

  const onTaskClickFromNotification = useCallback((taskId) => {
    setActiveTaskId(taskId);
  }, [setActiveTaskId]);
  const notifications = useNotifications({
    activeTaskId,
    onTaskClick: onTaskClickFromNotification,
  });

  const routing = useNotificationRouting(notifications.notify);

  const handleStatusEntry = useCallback((entry) => {
    routing.onStatusEntry(entry);
    const classification = classifyStatusEntry(entry);
    if (classification?.kind === NOTIFICATION_KIND.ATTENTION && classification.taskId) {
      attention.mark(classification.taskId);
    }
  }, [routing, attention]);

  const handleSessionEvent = useCallback((raw, taskId) => {
    routing.onSessionEvent(raw, taskId);
    if (!raw?.type || !taskId) { return; }
    if (raw.type === CLAUDE_EVENT.PERMISSION_REQUEST
        || raw.type === CLAUDE_EVENT.CONTROL_REQUEST) {
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

  const resizer = useResizable({
    storageKey: RIGHT_PANE_STORAGE_KEY,
    defaultWidth: RIGHT_PANE_DEFAULT_WIDTH,
    minWidth: RIGHT_PANE_MIN_WIDTH,
    maxWidth: RIGHT_PANE_MAX_WIDTH,
    anchor: 'right',
  });

  const activeSession = sessions.find((s) => s.task_id === activeTaskId) || null;
  const activeNeedsAttention = !!activeTaskId && attention.taskIds.has(activeTaskId);

  return (
    <>
      <ToastContainer />
      <SafetyBanner state={safetyState} />
      <Header
        notificationsEnabled={notifications.enabled}
        notificationsSupported={notifications.supported}
        notificationsPermission={notifications.permission}
        notificationKindPrefs={notifications.kindPrefs}
        onSetKindEnabled={notifications.setKindEnabled}
        onToggleNotifications={notifications.toggle}
        onRefresh={refresh}
      />
      <StatusBar
        latest={status.latest}
        stale={status.stale}
        connected={status.connected}
      />
      <Layout
        rightWidth={resizer.width}
        left={
          <TabList
            sessions={sessions}
            activeTaskId={activeTaskId}
            attentionTaskIds={attention.taskIds}
            onSelect={setActiveTaskId}
            onForget={handleForgetTask}
          />
        }
        center={
          <SessionDetail
            key={activeTaskId || '__none__'}
            session={activeSession}
            needsAttention={activeNeedsAttention}
            onActivity={handleSessionEvent}
          />
        }
        right={
          <RightPane
            activeTaskId={activeTaskId}
            workspaceVersion={workspaceVersion[activeTaskId] || 0}
            width={resizer.width}
            onResizePointerDown={resizer.onPointerDown}
            activityHistory={status.history}
          />
        }
      />
    </>
  );
}
