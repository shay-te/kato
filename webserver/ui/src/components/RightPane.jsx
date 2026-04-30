import { useState } from 'react';
import FilesTab from '../FilesTab.jsx';
import ChangesTab from '../ChangesTab.jsx';
import RightPaneResizer from './RightPaneResizer.jsx';

const TAB_FILES = 'files';
const TAB_CHANGES = 'changes';

export default function RightPane({
  activeTaskId,
  workspaceVersion = 0,
  width,
  onResizePointerDown,
}) {
  const [tab, setTab] = useState(TAB_FILES);

  return (
    <aside id="right-pane" style={{ width }}>
      <RightPaneResizer onPointerDown={onResizePointerDown} />
      <div id="right-pane-root">
        {activeTaskId ? (
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
              {tab === TAB_FILES && (
                <FilesTab
                  taskId={activeTaskId}
                  workspaceVersion={workspaceVersion}
                />
              )}
              {tab === TAB_CHANGES && (
                <ChangesTab
                  taskId={activeTaskId}
                  workspaceVersion={workspaceVersion}
                />
              )}
            </div>
          </div>
        ) : (
          <div className="right-pane-empty">
            Select a tab on the left to inspect files and changes.
          </div>
        )}
      </div>
    </aside>
  );
}
