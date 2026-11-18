import { useEffect, useState } from 'react';
import { readMaximizedPane, subscribeMaximizedPane } from '../utils/maximizedPanePref.js';

// The pane that has the whole window ('' = none), live: every reader — the
// layout grid and each maximize button — follows the one stored preference.
export function useMaximizedPane() {
  const [pane, setPane] = useState(() => readMaximizedPane());
  useEffect(() => subscribeMaximizedPane(setPane), []);
  return pane;
}
