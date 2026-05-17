export default function Layout({
  top, left, center, right,
  rightWidth, leftWidth,
}) {
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
      className={top ? 'has-top-tabs' : ''}
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
