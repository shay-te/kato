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

  useEffect(() => {
    writePersistedWidth(storageKey, width);
  }, [storageKey, width]);

  return { width, onPointerDown };
}
