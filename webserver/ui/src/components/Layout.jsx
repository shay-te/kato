import { useMaximizedPane } from '../hooks/useMaximizedPane.js';
import { MAXIMIZED_PANE, filesRailShown } from '../utils/maximizedPanePref.js';

export default function Layout({
  top, left, center, right,
  rightWidth, leftWidth,
}) {
  // A maximized pane (the chat, or the centre) takes the whole window and the
  // other two columns collapse. Read from the shared preference rather than a
  // prop: the controls that flip it live in the chat header (inside a
  // component App remounts on every task switch), the tab strip and the plan
  // view, and this grid is their sibling.
  const maximized = useMaximizedPane();
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
        // Only meaningful in the top-tabs shell, which is the only one with
        // the three columns to collapse.
        top && maximized === MAXIMIZED_PANE.CHAT ? 'is-chat-maximized' : '',
        top && maximized === MAXIMIZED_PANE.CENTER ? 'is-center-maximized' : '',
        top && filesRailShown(maximized) ? 'has-files-rail' : '',
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
