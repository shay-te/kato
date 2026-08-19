import { useEffect, useMemo, useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import {
  filterTaskPalette,
  nextPaletteIndex,
} from '../utils/taskPalette.js';

// Ctrl/Cmd+P task palette — the VS Code "Go to File" gesture, for tasks.
//
// Tab / Shift+Tab already walks the task strip, but that only helps when
// the task you want is a step or two away. With a strip full of tasks
// (most of them scrolled out of sight) the only way to reach one was to
// scroll and read every pill. This is the "I know its name, take me
// there" path.
//
// Selecting a row activates that task in the existing strip — it does not
// open a second surface. The palette is navigation, not a new place for
// state to live.
export default function TaskPalette({ sessions, nameFor, onSelect, onClose }) {
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const rows = useMemo(
    () => filterTaskPalette(sessions, query, nameFor),
    [sessions, query, nameFor],
  );

  // Typing changes the result set, so a highlight held from the previous
  // query would point at an unrelated row — and Enter would open the
  // wrong task. Reset to the top, which is also the best match.
  useEffect(() => { setHighlight(0); }, [query]);

  useEffect(() => {
    // Autofocus so the shortcut is a single gesture: the operator's next
    // keystroke is already the search term.
    if (inputRef.current) { inputRef.current.focus(); }
  }, []);

  // Keep the highlighted row visible while arrowing through a list
  // longer than the panel.
  useEffect(() => {
    const node = listRef.current?.querySelector('[data-highlighted="true"]');
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest' });
    }
  }, [highlight, rows]);

  function choose(taskId) {
    if (!taskId) { return; }
    onSelect(taskId);
    onClose();
  }

  function handleKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      setHighlight((current) => nextPaletteIndex(current, delta, rows.length));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      choose(rows[highlight]?.taskId);
    }
  }

  return (
    <div
      className="modal task-palette-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Go to task"
      onClick={(event) => {
        if (event.target === event.currentTarget) { onClose(); }
      }}
    >
      <div className="modal-card task-palette-card" onKeyDown={handleKeyDown}>
        <input
          ref={inputRef}
          id="task-palette-input"
          className="task-palette-input"
          type="text"
          value={query}
          placeholder="Go to task — type an id or name"
          aria-label="Search tasks"
          // The listbox is a sibling the input controls, so screen readers
          // announce the highlighted row as the operator arrows through it.
          role="combobox"
          aria-expanded="true"
          aria-controls="task-palette-list"
          aria-activedescendant={
            rows[highlight] ? `task-palette-row-${rows[highlight].taskId}` : undefined
          }
          autoComplete="off"
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul
          id="task-palette-list"
          className="task-palette-list"
          role="listbox"
          aria-label="Tasks"
          ref={listRef}
        >
          {rows.map((row, index) => (
            <li
              key={row.taskId}
              id={`task-palette-row-${row.taskId}`}
              role="option"
              aria-selected={index === highlight}
              data-highlighted={index === highlight ? 'true' : 'false'}
              className={cx('task-palette-row', index === highlight && 'highlighted')}
              // Mouse move, not hover CSS: the highlight is also the Enter
              // target, so pointing at a row has to move it or the click
              // and the keyboard would disagree about what is selected.
              onMouseMove={() => setHighlight(index)}
              onClick={() => choose(row.taskId)}
            >
              <span className="task-palette-id">{row.taskId}</span>
              <span className="task-palette-name">{row.displayName}</span>
            </li>
          ))}
          {rows.length === 0 && (
            <li className="task-palette-empty" role="option" aria-selected="false">
              No task matches “{query}”
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
