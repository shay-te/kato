import { cx } from '../utils/cx.js';
import { basenameOf } from '../utils/basenameOf.js';
import { useHorizontalWheelScroll } from '../hooks/useHorizontalWheelScroll.js';
import Icon from './Icon.jsx';
import DiffKindIcon from './DiffKindIcon.jsx';

// VS Code-style row of open-file tabs above the centre editor/diff
// pane. Every open file gets its own tab — opening a file never
// replaces another tab's content (see utils/fileTabs.js for the
// open/focus/close logic this renders). Deliberately simpler than the
// top task-tab strip (TabList.jsx): no pinning, no hover tooltip card,
// no chevron buttons / click-and-hold scroll — just the shared
// wheel-scroll remap (a mouse wheel would otherwise scroll the page,
// not this strip) plus native drag-the-thumb scrolling for overflow.
export default function FileTabStrip({
  tabs, activeKey, onSelect, onClose, onToggleView,
}) {
  // Callback ref, not useRef + useEffect — this component returns
  // null (no DOM node at all) whenever tabs is empty, which a plain
  // useEffect-on-a-stable-ref-object would miss the very first time
  // a tab actually opens (see the hook for the full explanation).
  const wheelRef = useHorizontalWheelScroll();
  if (!tabs || tabs.length === 0) { return null; }
  return (
    <nav className="file-tab-strip" aria-label="Open files" ref={wheelRef}>
      <ul className="file-tab-list">
        {tabs.map((tab) => (
          <FileTab
            key={tab.key}
            tab={tab}
            active={tab.key === activeKey}
            onSelect={onSelect}
            onClose={onClose}
            onToggleView={onToggleView}
          />
        ))}
      </ul>
    </nav>
  );
}

function FileTab({
  tab, active, onSelect, onClose, onToggleView,
}) {
  const name = basenameOf(tab.relativePath || tab.absolutePath) || tab.relativePath;
  const title = tab.repoId ? `${tab.repoId}/${tab.relativePath}` : tab.relativePath;
  const isDiff = tab.view === 'diff';
  // A diff tab shows the file's change kind (the green + / red − / edit
  // glyph the tree uses); a plain-file tab just shows a file.
  const tabIcon = isDiff
    ? <DiffKindIcon kind={tab.kind} />
    : <Icon name="file" />;

  function handleSelect() {
    onSelect(tab.key);
  }
  function handleToggleView(event) {
    // Not a tab selection — the click stops here or the strip would
    // also focus the tab underneath it.
    event.stopPropagation();
    onToggleView(tab.key);
  }
  function handleClose(event) {
    event.stopPropagation();
    onClose(tab.key);
  }
  function handleAuxClick(event) {
    // Middle-click closes, matching every browser's own tab strip.
    if (event.button !== 1) { return; }
    event.preventDefault();
    onClose(tab.key);
  }
  function handleKeyDown(event) {
    // Ctrl/Cmd+W closes the focused tab — the standard "close this
    // tab" shortcut, scoped to when a file tab actually has focus so
    // it doesn't fight the browser's own tab-close binding globally.
    const isW = event.key === 'w' || event.key === 'W';
    if (isW && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      onClose(tab.key);
    }
  }

  return (
    <li
      className={cx('file-tab', active && 'active')}
      title={title}
      onClick={handleSelect}
      onAuxClick={handleAuxClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
    >
      {/* The tab's leading icon carries BOTH jobs the diff pane's header
          used to: it shows how the file changed (+ / − / edit) while the
          diff is up, and clicking it switches diff ⇄ plain file. That
          header is gone — it was a row restating this tab. */}
      {typeof onToggleView === 'function' ? (
        <button
          type="button"
          className="file-tab-view-toggle tooltip-start"
          onClick={handleToggleView}
          data-tooltip={isDiff ? 'View file (no diff)' : 'View diff'}
          aria-label={isDiff ? `View ${name} without the diff` : `View the diff for ${name}`}
        >
          {tabIcon}
        </button>
      ) : (
        tabIcon
      )}
      <span className="file-tab-label">{name}</span>
      <button
        type="button"
        className="file-tab-close-btn"
        aria-label={`Close ${name}`}
        onClick={handleClose}
      >
        <Icon name="xmark" />
      </button>
    </li>
  );
}
