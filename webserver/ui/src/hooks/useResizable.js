import { useCallback, useEffect, useRef, useState } from 'react';
import { readPersistedWidth, writePersistedWidth } from '../utils/resizableStorage.js';

export function useResizable({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
  anchor = 'right',
}) {
  const clamp = useCallback(
    (value) => Math.min(maxWidth, Math.max(minWidth, value)),
    [maxWidth, minWidth],
  );

  const [width, setWidth] = useState(() => {
    const stored = readPersistedWidth(storageKey);
    return stored !== null ? clamp(stored) : defaultWidth;
  });

  // Bounds can arrive AFTER first render — a tab measures its own label in a
  // layout effect, so the hook initialises against fallbacks and would then
  // keep a width that the real min/max never applied to (the "grip renders
  // but won't drag" bug). Re-clamp whenever they change.
  useEffect(() => {
    setWidth((current) => clamp(current));
  }, [clamp]);

  const startStateRef = useRef(null);

  const onPointerDown = useCallback((event) => {
    event.preventDefault();
    startStateRef.current = { startX: event.clientX, startWidth: width };
    // Two classes, two scopes. ``kato-resizing`` is GLOBAL and only drives
    // document-wide drag ergonomics (col-resize cursor, no text selection).
    // The blue active visual hangs off ``is-dragging`` on THIS handle —
    // painting it from the body class lit up the other pane's handle too,
    // which read as "both boundaries are moving" when only one was.
    const handle = event.currentTarget;
    document.body.classList.add('kato-resizing');
    if (handle) { handle.classList.add('is-dragging'); }

    const onMove = (moveEvent) => {
      if (!startStateRef.current) { return; }
      const dx = moveEvent.clientX - startStateRef.current.startX;
      const delta = anchor === 'right' ? -dx : dx;
      setWidth(clamp(startStateRef.current.startWidth + delta));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('kato-resizing');
      if (handle) { handle.classList.remove('is-dragging'); }
      startStateRef.current = null;
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [anchor, clamp, width]);

  // Re-clamp whenever the bounds change. ``maxWidth`` is dynamic for the
  // chat pane (viewport − centre-min), so a viewport shrink lowers it; without
  // this the persisted/last width could exceed the new max and squeeze the
  // neighbouring pane until the operator drags. ``clamp`` is identity when the
  // width is already in range, so this is a no-op in the common case.
  useEffect(() => {
    setWidth((current) => clamp(current));
  }, [clamp]);

  useEffect(() => {
    writePersistedWidth(storageKey, width);
  }, [storageKey, width]);

  return { width, onPointerDown };
}
