import { useEffect, useState } from 'react';

import {
  readChatMaximized,
  subscribeChatMaximized,
} from '../utils/chatMaximizedPref.js';

export default function Layout({
  top, left, center, right,
  rightWidth, leftWidth,
}) {
  // Maximized = the chat takes the whole window and the files/preview columns
  // collapse. Read from the shared preference rather than a prop, because the
  // control that flips it lives in the chat header — inside a component App
  // remounts on every task switch — and this grid is its sibling.
  const [maximized, setMaximized] = useState(() => readChatMaximized());
  useEffect(() => subscribeChatMaximized(setMaximized), []);
  // Pre-build the style object so we only set CSS vars that were
  // actually passed — Layout is rendered by both the new top-tabs
  // shell and the legacy sidebar shell, only one of which needs
  // ``--left-pane-width``.
  const style = {};
  if (rightWidth !== undefined && rightWidth !== null) {
    style['--right-pane-width'] = `${rightWidth}px`;
  }
  if (leftWidth !== undefined && leftWidth !== null) {
    style['--left-pane-width'] = `${leftWidth}px`;
  }
  return (
    <div
      id="layout"
      className={[
        top ? 'has-top-tabs' : '',
        // Only meaningful in the top-tabs shell, which is the only one with a
        // chat column to maximize.
        top && maximized ? 'is-chat-maximized' : '',
      ].filter(Boolean).join(' ')}
      style={style}
    >
      {top}
      {/* Full-width task-header bar: sits UNDER the tab strip and
          ABOVE all three panels. SessionDetail portals its
          SessionHeader (title + actions + Claude status + search)
          into this slot. Always rendered (even empty) so the portal
          target is stable; only meaningful in the top-tabs shell. */}
      {top ? <div id="task-header-slot" /> : null}
      {left}
      {center}
      {right}
    </div>
  );
}
