import { useEffect, useRef } from 'react';

// The ``@``-mention autocomplete dropdown for the composer. Pure presentational:
// MessageForm owns the textarea, the filtered ``items``, the ``activeIndex`` and
// all keyboard handling; this just renders the list and reports pointer intent.
//
// Rows use onMouseDown (not onClick) + preventDefault so picking a file does NOT
// blur the textarea first — mousedown fires before blur, so focus/caret stay put
// and MessageForm can insert the reference and restore the caret.
export default function ComposerMentionMenu({ items, activeIndex, onSelect, onHover }) {
  const listRef = useRef(null);

  // Keep the highlighted row visible as the operator arrows through a long list.
  useEffect(() => {
    const row = listRef.current?.children?.[activeIndex];
    if (row && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'nearest' });
    }
  }, [activeIndex]);

  return (
    <div className="composer-mention-menu" role="listbox" aria-label="Workspace files">
      {items.length === 0 ? (
        <div className="composer-mention-empty">No matching files</div>
      ) : (
        <ul ref={listRef} className="composer-mention-list">
          {items.map((file, index) => (
            <li
              key={`${file.repoId}/${file.relativePath}`}
              role="option"
              aria-selected={index === activeIndex}
              className={`composer-mention-item${index === activeIndex ? ' is-active' : ''}`}
              onMouseDown={(event) => { event.preventDefault(); onSelect(file); }}
              onMouseEnter={() => onHover && onHover(index)}
            >
              <span className="composer-mention-name">{file.name}</span>
              <span className="composer-mention-path">{file.relativePath}</span>
              {file.repoId && (
                <span className="composer-mention-repo">{file.repoId}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
