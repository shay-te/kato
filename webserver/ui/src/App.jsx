import { useCallback, useState } from 'react';
import Header from './components/Header.jsx';
import Layout from './components/Layout.jsx';
import RightPane from './components/RightPane.jsx';
import SessionDetail from './components/SessionDetail.jsx';
import StatusBar from './components/StatusBar.jsx';
import TabList from './components/TabList.jsx';
import { useNotifications } from './hooks/useNotifications.js';
import { useNotificationRouting } from './hooks/useNotificationRouting.js';
import { useResizable } from './hooks/useResizable.js';
import { useSessions } from './hooks/useSessions.js';
import { useStatusFeed } from './hooks/useStatusFeed.js';
import { useTaskAttention } from './hooks/useTaskAttention.js';
import { classifyStatusEntry } from './utils/classifyStatusEntry.js';

const RIGHT_PANE_DEFAULT_WIDTH = 380;
const RIGHT_PANE_MIN_WIDTH = 220;
const RIGHT_PANE_MAX_WIDTH = 900;
const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';

export default function App() {
  const [activeTaskId, setActiveTaskIdState] = useState('');
  const { sessions, refresh } = useSessions();
  const attention = useTaskAttention();

  const setActiveTaskId = useCallback((taskId) => {
    setActiveTaskIdState(taskId);
    attention.clear(taskId);
  }, [attention]);

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
    if (classification?.kind === 'attention' && classification.taskId) {
      attention.mark(classification.taskId);
    }
  }, [routing, attention]);

  const handleSessionEvent = useCallback((raw, taskId) => {
    routing.onSessionEvent(raw, taskId);
    if (!raw?.type || !taskId) { return; }
    if (raw.type === 'permission_request' || raw.type === 'control_request') {
      attention.mark(taskId);
    } else if (raw.type === 'result') {
      attention.clear(taskId);
    }
  }, [routing, attention]);

  const status = useStatusFeed(handleStatusEntry);

  const resizer = useResizable({
    storageKey: RIGHT_PANE_STORAGE_KEY,
    defaultWidth: RIGHT_PANE_DEFAULT_WIDTH,
    minWidth: RIGHT_PANE_MIN_WIDTH,
    maxWidth: RIGHT_PANE_MAX_WIDTH,
    anchor: 'right',
  });

  const activeSession = sessions.find((s) => s.task_id === activeTaskId) || null;

  return (
    <>
      <Header
        notificationsEnabled={notifications.enabled}
        notificationsSupported={notifications.supported}
        onToggleNotifications={notifications.toggle}
        onRefresh={refresh}
      />
      <StatusBar
        latest={status.latest}
        history={status.history}
        stale={status.stale}
      />
      <Layout
        rightWidth={resizer.width}
        left={
          <TabList
            sessions={sessions}
            activeTaskId={activeTaskId}
            attentionTaskIds={attention.taskIds}
            onSelect={setActiveTaskId}
          />
        }
        center={
          <SessionDetail
            key={activeTaskId || '__none__'}
            session={activeSession}
            onActivity={handleSessionEvent}
          />
        }
        right={
          <RightPane
            activeTaskId={activeTaskId}
            width={resizer.width}
            onResizePointerDown={resizer.onPointerDown}
          />
        }
      />
    </>
  );
}
