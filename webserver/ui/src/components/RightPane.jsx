import { useEffect, useState } from 'react';
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
  onOpenFile,
}) {
  const [tab, setTab] = useState(TAB_FILES);
  // Bumped each time Cmd/Ctrl+P fires. ``FilesTab`` watches this and
  // focuses its filter input on every increment. Using a counter
  // (vs a boolean toggle) so consecutive invocations always trigger
  // the focus effect — repeated Cmd+P presses re-focus the input
  // even if it's already mounted.
  const [focusFilterSignal, setFocusFilterSignal] = useState(0);

  // VS Code's Cmd+P / Ctrl+P opens the file picker. Same shortcut
  // here flips the right pane to Files and focuses the search box.
  // Only intercepted when there's an active task — otherwise the
  // browser keeps its default Print binding so operators on the
  // empty-state screen aren't surprised.
  useEffect(() => {
    if (!activeTaskId) { return; }
    function onKeyDown(event) {
      const isP = event.key === 'p' || event.key === 'P';
      if (!isP) { return; }
      const meta = event.metaKey || event.ctrlKey;
      if (!meta) { return; }
      if (event.shiftKey || event.altKey) { return; }
      event.preventDefault();
      setTab(TAB_FILES);
      setFocusFilterSignal((tick) => tick + 1);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeTaskId]);

  const filesBody = tab === TAB_FILES && (
    <FilesTab
      taskId={activeTaskId}
      workspaceVersion={workspaceVersion}
      focusFilterSignal={focusFilterSignal}
      onOpenFile={onOpenFile}
    />
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

  // ``width`` is forwarded as an inline style ONLY when an
  // explicit value was passed. In the new top-tabs layout the
  // pane lives in a fixed-width grid cell and must not set
  // ``width``: setting it (even to ``undefined``) used to render
  // ``style="width: undefined"`` which most browsers ignore but
  // some tools normalise to ``auto``, plus the previous prop
  // default ``width={resizer.width}`` made the pane overflow its
  // cell. Letting the grid track decide the width is the right
  // default; only the legacy resizable-sidebar layout opts in.
  const inlineStyle = (width !== undefined && width !== null && width !== '')
    ? { width }
    : undefined;
  const resizer = typeof onResizePointerDown === 'function'
    ? <RightPaneResizer onPointerDown={onResizePointerDown} />
    : null;
  return (
    <aside id="right-pane" style={inlineStyle}>
      {resizer}
      <div id="right-pane-root">
        {sessionBody}
      </div>
    </aside>
  );
}
