import { useEffect, useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import { basenameOf } from '../utils/basenameOf.js';
import { useHorizontalWheelScroll } from '../hooks/useHorizontalWheelScroll.js';
import Icon from './Icon.jsx';
import DiffKindIcon from './DiffKindIcon.jsx';
import { markdownViewFor } from '../utils/markdownView.js';

// VS Code-style row of open-file tabs above the centre editor/diff
// pane. Every open file gets its own tab — opening a file never
// replaces another tab's content (see utils/fileTabs.js for the
// open/focus/close logic this renders). Deliberately simpler than the
// top task-tab strip (TabList.jsx): no pinning, no hover tooltip card,
// no chevron buttons / click-and-hold scroll — just the shared
// wheel-scroll remap (a mouse wheel would otherwise scroll the page,
// not this strip) plus native drag-the-thumb scrolling for overflow.
const NO_DRAG = { key: '', over: '' };

export default function FileTabStrip({
  tabs, activeKey, onSelect, onClose, onToggleView, onToggleMarkdownView,
  onCloseAll, onCloseOthers, onTogglePin, onReorder,
  // Bumped ONLY when the operator opened a file from the tree. Scrolling
  // the tab into view is a response to that click and nothing else — see
  // the effect below.
  revealRequestId = 0,
}) {
  // Drag-to-reorder state. ``key`` is the tab being dragged, ``over`` the
  // one it is currently hovering — kept together so a single setState can
  // never leave a stale highlight behind after a drop.
  const [drag, setDrag] = useState(NO_DRAG);
  // Callback ref, not useRef + useEffect — this component returns
  // null (no DOM node at all) whenever tabs is empty, which a plain
  // useEffect-on-a-stable-ref-object would miss the very first time
  // a tab actually opens (see the hook for the full explanation).
  const wheelRef = useHorizontalWheelScroll();
  const activeTabRef = useRef(null);
  const lastRevealRef = useRef(revealRequestId);
  const [menu, setMenu] = useState(null);

  // Bring the active tab into view — but ONLY when the reveal id changed,
  // i.e. the operator just clicked a file in the tree. Deliberately NOT
  // keyed on activeKey or on tabs: those change while scrolling, on
  // refresh, and on view toggles, and re-centring then would drag the
  // strip back under the operator's cursor every time they tried to look
  // at something else. A scroll is intent too, and it must win.
  useEffect(() => {
    if (revealRequestId === lastRevealRef.current) { return; }
    lastRevealRef.current = revealRequestId;
    const node = activeTabRef.current;
    // Guarded: jsdom has no scrollIntoView, and neither do a couple of
    // older embedded browsers. Not being able to scroll is cosmetic —
    // it must never take the strip down with it.
    if (typeof node?.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    }
  }, [revealRequestId]);

  useEffect(() => {
    if (!menu) { return undefined; }
    const dismiss = () => setMenu(null);
    window.addEventListener('click', dismiss);
    window.addEventListener('keydown', dismiss);
    window.addEventListener('resize', dismiss);
    return () => {
      window.removeEventListener('click', dismiss);
      window.removeEventListener('keydown', dismiss);
      window.removeEventListener('resize', dismiss);
    };
  }, [menu]);

  if (!tabs || tabs.length === 0) { return null; }
  const draggedTab = drag.key ? tabs.find((tab) => tab.key === drag.key) : null;
  return (
    <nav className="file-tab-strip" aria-label="Open files" ref={wheelRef}>
      <ul className="file-tab-list">
        {tabs.map((tab) => (
          <FileTab
            key={tab.key}
            tab={tab}
            active={tab.key === activeKey}
            nodeRef={tab.key === activeKey ? activeTabRef : null}
            onSelect={onSelect}
            onClose={onClose}
            onToggleView={onToggleView}
            onToggleMarkdownView={onToggleMarkdownView}
            dragging={drag.key === tab.key}
            dropTarget={drag.over === tab.key && drag.key !== tab.key}
            // Pins are a block at the front of the strip, so a tab can only
            // be dropped among its own group. Refusing the drop (rather than
            // silently snapping it elsewhere) keeps the pinned block honest.
            canDrop={
              !!draggedTab
              && draggedTab.key !== tab.key
              && !!draggedTab.pinned === !!tab.pinned
            }
            onDragStart={(key) => setDrag({ key, over: '' })}
            onDragEnd={() => setDrag(NO_DRAG)}
            onDragOver={(key) => setDrag((prev) => (
              prev.over === key ? prev : { ...prev, over: key }
            ))}
            onDragLeave={() => setDrag((prev) => (
              prev.over ? { ...prev, over: '' } : prev
            ))}
            onDrop={(key) => {
              if (drag.key && drag.key !== key && onReorder) {
                onReorder(drag.key, key);
              }
              setDrag(NO_DRAG);
            }}
            onContextMenu={(event) => {
              event.preventDefault();
              setMenu({ key: tab.key, tab, x: event.clientX, y: event.clientY });
            }}
          />
        ))}
      </ul>
      {menu && (
        <FileTabMenu
          menu={menu}
          tabCount={tabs.length}
          onClose={onClose}
          onCloseAll={onCloseAll}
          onCloseOthers={onCloseOthers}
          onTogglePin={onTogglePin}
          dismiss={() => setMenu(null)}
        />
      )}
    </nav>
  );
}


function FileTabMenu({
  menu, tabCount, onClose, onCloseAll, onCloseOthers, onTogglePin, dismiss,
}) {
  const { tab, x, y } = menu;
  const name = basenameOf(tab.relativePath || tab.absolutePath) || tab.relativePath;

  function run(action) {
    return (event) => {
      event.stopPropagation();
      dismiss();
      action();
    };
  }

  async function copyName() {
    // The file NAME, which is what the tab shows and what someone
    // right-clicking a tab is almost always after — the full path is
    // already on the tab's title tooltip.
    try {
      await navigator.clipboard.writeText(name);
    } catch {
      // Clipboard permission denied / insecure context: nothing to
      // recover, and a failed copy must not take the menu down noisily.
    }
  }

  const items = [
    { label: 'Close', action: () => onClose(tab.key), enabled: true },
    {
      label: 'Close others',
      action: () => onCloseOthers && onCloseOthers(tab.key),
      enabled: typeof onCloseOthers === 'function' && tabCount > 1,
    },
    {
      label: 'Close all',
      action: () => onCloseAll && onCloseAll(),
      enabled: typeof onCloseAll === 'function',
    },
    {
      label: tab.pinned ? 'Unpin tab' : 'Pin tab',
      action: () => onTogglePin && onTogglePin(tab.key),
      enabled: typeof onTogglePin === 'function',
    },
    { label: 'Copy file name', action: copyName, enabled: true },
  ];

  return (
    <ul
      className="files-tab-context-menu file-tab-menu"
      role="menu"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
    >
      {items.map((item) => (
        <li key={item.label} role="none">
          <button
            type="button"
            role="menuitem"
            className="files-tab-context-menu-item"
            disabled={!item.enabled}
            onClick={run(item.action)}
          >
            {item.label}
          </button>
        </li>
      ))}
    </ul>
  );
}

function FileTab({
  tab, active, nodeRef, onSelect, onClose, onToggleView, onToggleMarkdownView,
  onContextMenu, dragging, dropTarget, canDrop,
  onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
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
  // '' for anything that isn't markdown, so the switch only appears where
  // there is a rendered view to switch to. A diff is already a source view —
  // rendering markdown would hide the +/- lines, so no switch there either.
  const mdView = isDiff ? '' : markdownViewFor(tab);
  const isPreview = mdView === 'preview';
  function handleToggleMarkdownView(event) {
    event.stopPropagation();
    onToggleMarkdownView(tab.key);
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
  // Where the press that could become a drag started. Same guard as the task
  // strip: every control here (close, the view toggles) is a plain child of a
  // draggable <li>, and the drag model picks the nearest ancestor with
  // ``draggable=true`` — a button is not a barrier, and ``draggable={false}``
  // on the child is not one either. Unguarded, pressing × and drifting a few
  // pixels reorders the tab instead of closing it, and the drag suppresses
  // the click so the × does nothing.
  const pressedControlRef = useRef(false);

  function handleMouseDownCapture(event) {
    pressedControlRef.current = !!event?.target?.closest?.('button, input');
  }

  function handleDragStart(event) {
    if (pressedControlRef.current) {
      // ``preventDefault`` on ``dragstart`` is the spec's own "no drag".
      event.preventDefault();
      return;
    }
    // The key travels in the dataTransfer payload so a drop knows what
    // moved even though React state updates are async.
    event.dataTransfer.effectAllowed = 'move';
    try {
      event.dataTransfer.setData('text/plain', tab.key);
    } catch (_err) { /* older browsers restrict setData; state still carries it */ }
    onDragStart(tab.key);
  }
  function handleDragOver(event) {
    // Only a same-group drop is legal (pins stay a block at the front), so
    // the cursor has to say "no" over an illegal target rather than letting
    // the operator complete a drag that will be refused.
    if (!canDrop) { return; }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    onDragOver(tab.key);
  }
  function handleDrop(event) {
    if (!canDrop) { return; }
    event.preventDefault();
    onDrop(tab.key);
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
      ref={nodeRef}
      className={cx(
        'file-tab',
        active && 'active',
        tab.pinned && 'pinned',
        dragging && 'dragging',
        dropTarget && 'drop-target',
      )}
      title={title}
      draggable
      onMouseDownCapture={handleMouseDownCapture}
      onClick={handleSelect}
      onContextMenu={onContextMenu}
      onAuxClick={handleAuxClick}
      onKeyDown={handleKeyDown}
      onDragStart={handleDragStart}
      onDragEnd={onDragEnd}
      onDragOver={handleDragOver}
      onDragLeave={onDragLeave}
      onDrop={handleDrop}
      tabIndex={0}
    >
      {/* Leading icon is STATUS ONLY — how the file changed (+ / − / edit)
          while the diff is up, a plain file glyph otherwise. It used to
          double as the diff ⇄ file switch, but a file-type glyph in the
          leading slot reads as decoration: nothing marked it as clickable,
          so the toggle was effectively undiscoverable. It now has its own
          control next to the close X, where a tab's actions live. */}
      {tabIcon}
      <span className="file-tab-label">{name}</span>
      {mdView && typeof onToggleMarkdownView === 'function' && (
        <button
          type="button"
          className="file-tab-view-toggle tooltip-start"
          onClick={handleToggleMarkdownView}
          data-tooltip={isPreview ? 'View the markdown source' : 'View the rendered preview'}
          aria-label={
            isPreview
              ? `View the ${name} markdown source`
              : `View the rendered preview of ${name}`
          }
        >
          {/* Shows the TARGET view, like the diff toggle beside it. */}
          <Icon name={isPreview ? 'code' : 'eye'} />
        </button>
      )}
      {typeof onToggleView === 'function' && (
        <button
          type="button"
          className="file-tab-view-toggle tooltip-start"
          onClick={handleToggleView}
          data-tooltip={isDiff ? 'View the whole file (no diff)' : 'View the diff'}
          aria-label={isDiff ? `View the whole ${name} file` : `View the diff for ${name}`}
        >
          {/* Shows the TARGET view, matching the tooltip: the diff glyph
              means "take me to the diff". */}
          <Icon name={isDiff ? 'file' : 'diff'} />
        </button>
      )}
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
