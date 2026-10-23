import { useState } from 'react';
import { cx } from '../utils/cx.js';
import ModalShell from './ModalShell.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';

// Shared search-picker modal: ModalShell scrim/header + a search input +
// a selectable list + a Cancel/Confirm footer. The three picker modals
// (adopt session / add repository / adopt task) differ only in how they
// load + filter their items, how a row renders, and what confirm does —
// all injected as props. This component owns the generic picker state:
// the highlighted row and the in-flight confirm guard.
//
// The CALLER owns ``query`` (passed in with ``onQueryChange``) so it can
// drive either a server-side search (feed query into its fetch) or a
// client-side filter, and passes the already-computed ``items`` to render.
// ``onConfirm(selectedItem)`` does the caller's API call + toast + close;
// busy/disabled handling is managed here.
//
// ``multiple`` turns it into a checklist: any number of rows can be ticked and
// ``onConfirm`` receives the array, in the order they were ticked. The ticks
// belong to the picker, NOT to the filtered ``items``: narrowing the search
// never drops a pick, and a pick the current filter hides is still listed above
// the list (and can be taken back from there) — so a person can search one
// thing, tick it, search another, tick that too, and confirm them all.
export default function SearchPickerModal({
  ariaLabel,
  title,
  extraClass,
  onClose,
  helpText,
  searchPlaceholder,
  query,
  onQueryChange,
  items,
  loading,
  error,
  loadingText,
  emptyText,
  getItemId,
  renderRow,
  rowClassName,
  onConfirm,
  confirmLabel,
  busyLabel,
  multiple = false,
}) {
  const [selectedId, setSelectedId] = useState('');
  // id -> item, in the order ticked. Keeps the ITEM too, because once a filter
  // hides the row the item is no longer in ``items`` to look up.
  const [picked, setPicked] = useState(() => new Map());
  const [busy, setBusy] = useState(false);
  const selectedItem = items.find((item) => getItemId(item) === selectedId);
  const pickedItems = [...picked.values()];
  const hasSelection = multiple ? picked.size > 0 : !!selectedItem;
  const allShownPicked = items.length > 0 && items.every((item) => picked.has(getItemId(item)));

  function togglePicked(item) {
    const id = getItemId(item);
    setPicked((current) => {
      const next = new Map(current);
      if (next.has(id)) { next.delete(id); } else { next.set(id, item); }
      return next;
    });
  }

  function pickAllShown() {
    setPicked((current) => {
      const next = new Map(current);
      items.forEach((item) => next.set(getItemId(item), item));
      return next;
    });
  }

  async function confirm() {
    if (!hasSelection || busy) { return; }
    setBusy(true);
    try {
      await onConfirm(multiple ? pickedItems : selectedItem);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell
      ariaLabel={ariaLabel}
      title={title}
      extraClass={extraClass}
      onClose={onClose}
    >
      <p className="adopt-session-modal-help">{helpText}</p>
      <input
        type="text"
        className="adopt-session-search"
        placeholder={searchPlaceholder}
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        autoFocus
      />
      {multiple && (
        <div className="adopt-session-picked">
          <div className="adopt-session-bulk">
            <span className="adopt-session-picked-count">
              {picked.size === 0 ? 'Nothing selected' : `${picked.size} selected`}
            </span>
            <button
              type="button"
              className="adopt-session-bulk-button"
              disabled={busy || items.length === 0 || allShownPicked}
              onClick={pickAllShown}
            >
              Select all shown
            </button>
            <button
              type="button"
              className="adopt-session-bulk-button"
              disabled={busy || picked.size === 0}
              onClick={() => setPicked(new Map())}
            >
              Deselect all
            </button>
          </div>
          {picked.size > 0 && (
            <ul className="adopt-session-chips" aria-label="Selected">
              {pickedItems.map((item) => {
                const id = getItemId(item);
                return (
                  <li key={id} className="adopt-session-chip">
                    <span>{id}</span>
                    <button
                      type="button"
                      className="adopt-session-chip-remove"
                      aria-label={`Remove ${id}`}
                      disabled={busy}
                      onClick={() => togglePicked(item)}
                    >
                      ×
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <div className="adopt-session-list">
        {loading && (
          <div className="adopt-session-empty">{loadingText}</div>
        )}
        {!loading && error && (
          <div className="adopt-session-empty adopt-session-error">{error}</div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="adopt-session-empty">{emptyText}</div>
        )}
        {!loading && !error && items.map((item) => {
          const id = getItemId(item);
          const checked = multiple ? picked.has(id) : id === selectedId;
          return (
            <button
              type="button"
              key={id}
              className={cx(
                'adopt-session-row',
                checked && 'is-selected',
                rowClassName && rowClassName(item),
              )}
              {...(multiple ? { role: 'checkbox', 'aria-checked': checked } : {})}
              onClick={() => (multiple ? togglePicked(item) : setSelectedId(id))}
            >
              {multiple && (
                <span className="adopt-session-check" aria-hidden="true">{checked ? '✓' : ''}</span>
              )}
              {renderRow(item)}
            </button>
          );
        })}
      </div>
      <ModalFooterActions
        onCancel={onClose}
        onConfirm={confirm}
        busy={busy}
        canConfirm={hasSelection}
        confirmLabel={multiple && picked.size > 1 ? `${confirmLabel} (${picked.size})` : confirmLabel}
        busyLabel={busyLabel}
      />
    </ModalShell>
  );
}
