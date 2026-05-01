import { useState } from 'react';
import FilesTab from '../FilesTab.jsx';
import ChangesTab from '../ChangesTab.jsx';
import OrchestratorActivityFeed from './OrchestratorActivityFeed.jsx';
import RightPaneResizer from './RightPaneResizer.jsx';

const TAB_FILES = 'files';
const TAB_CHANGES = 'changes';

export default function RightPane({
  activeTaskId,
  workspaceVersion = 0,
  width,
  onResizePointerDown,
  activityHistory = [],
}) {
  const [tab, setTab] = useState(TAB_FILES);

  const filesBody = tab === TAB_FILES && (
    <FilesTab taskId={activeTaskId} workspaceVersion={workspaceVersion} />
  );
  const changesBody = tab === TAB_CHANGES && (
    <ChangesTab taskId={activeTaskId} workspaceVersion={workspaceVersion} />
  );
  const filesTabClass = tab === TAB_FILES ? 'active' : '';
  const changesTabClass = tab === TAB_CHANGES ? 'active' : '';
  function showFiles() { setTab(TAB_FILES); }
  function showChanges() { setTab(TAB_CHANGES); }
  const sessionBody = activeTaskId ? (
    <div className="right-pane">
      <nav className="right-pane-tabs">
        <button
          type="button"
          className={filesTabClass}
          data-tooltip="Browse the workspace file tree for this task."
          onClick={showFiles}
        >
          Files
        </button>
        <button
          type="button"
          className={changesTabClass}
          data-tooltip="Review the diff: every file the agent has modified on this task's branch."
          onClick={showChanges}
        >
          Changes
        </button>
      </nav>
      <div className="right-pane-body">
        {filesBody}
        {changesBody}
      </div>
    </div>
  ) : (
    <OrchestratorActivityFeed history={activityHistory} />
  );

  return (
    <aside id="right-pane" style={{ width }}>
      <RightPaneResizer onPointerDown={onResizePointerDown} />
      <div id="right-pane-root">
        {sessionBody}
      </div>
    </aside>
  );
}
