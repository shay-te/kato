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

  const startStateRef = useRef(null);

  const onPointerDown = useCallback((event) => {
    event.preventDefault();
    startStateRef.current = { startX: event.clientX, startWidth: width };
    document.body.classList.add('kato-resizing');

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
