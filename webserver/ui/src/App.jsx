import { useEffect, useState } from 'react';
import FilesTab from './FilesTab.jsx';
import ChangesTab from './ChangesTab.jsx';

const TAB_FILES = 'files';
const TAB_CHANGES = 'changes';

function readInitialTask() {
  if (typeof window === 'undefined') { return ''; }
  if (typeof window.katoGetActiveTaskId === 'function') {
    return window.katoGetActiveTaskId() || '';
  }
  return window.__katoActiveTaskId || '';
}

// Top-level component for the right-side pane. Subscribes to the
// `kato:active-task` CustomEvent so the vanilla-JS shell can tell us
// which task to display without React having to know anything about
// tabs, SSE, or chat state.
export default function App() {
  // Lazy initial: read whatever task vanilla-JS has already activated.
  // The bundle is heavy (~665kb) and React mounts after the rest of the
  // page loads — a fast tab-click before mount would otherwise lose the
  // CustomEvent and leave the pane stuck on the empty state.
  const [activeTaskId, setActiveTaskId] = useState(() => readInitialTask());
  const [tab, setTab] = useState(TAB_FILES);

  useEffect(() => {
    function handleTaskChange(event) {
      const taskId = (event && event.detail && event.detail.taskId) || '';
      setActiveTaskId(taskId);
    }
    window.addEventListener('kato:active-task', handleTaskChange);
    // Resync once after subscribe — covers the race where vanilla-JS
    // dispatched an event between `useState` initialization and now.
    setActiveTaskId(readInitialTask());
    return () => window.removeEventListener('kato:active-task', handleTaskChange);
  }, []);

  if (!activeTaskId) {
    return (
      <div className="right-pane-empty">
        Select a tab on the left to inspect files and changes.
      </div>
    );
  }

  return (
    <div className="right-pane">
      <nav className="right-pane-tabs">
        <button
          type="button"
          className={tab === TAB_FILES ? 'active' : ''}
          onClick={() => setTab(TAB_FILES)}
        >
          Files
        </button>
        <button
          type="button"
          className={tab === TAB_CHANGES ? 'active' : ''}
          onClick={() => setTab(TAB_CHANGES)}
        >
          Changes
        </button>
      </nav>
      <div className="right-pane-body">
        {tab === TAB_FILES && <FilesTab taskId={activeTaskId} />}
        {tab === TAB_CHANGES && <ChangesTab taskId={activeTaskId} />}
      </div>
    </div>
  );
}
