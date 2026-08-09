import { cx } from '../utils/cx.js';
import PaneResizer from './PaneResizer.jsx';

// The shared shell for every main-screen panel — the files tree, the
// centre editor/diff/plan column, and the chat. Each renders as a
// rounded, outlined card separated from its neighbours by the layout
// gutter (``#layout.has-top-tabs`` supplies the padding + column-gap).
//
// The card chrome lives ENTIRELY on the two classes rendered here
// (``.panel-card`` / ``.panel-card-content``) — a panel must never
// style its own outline, radius, or clipping. To add a fourth panel,
// render it through this component; do not add another id to the
// stylesheet.
//
// Two structural details the CSS depends on:
//   - the content wrapper, not the card, is what clips to the rounded
//     corners: the resize handle is a card child that deliberately
//     sits out in the gutter (``left``/``right: -9px``) and would be
//     cut off by a clip on the card itself;
//   - the card is a flex column and the content wrapper is its
//     ``flex: 1`` row, so panels only have to fill the wrapper.
//
// ``contentId`` / ``contentClassName`` let a panel keep the hook its
// existing rules already key on (e.g. ``#right-pane-root``) instead of
// growing a redundant extra wrapper.
export default function PanelCard({
  as: Tag = 'div',
  id,
  className = '',
  style,
  contentId,
  contentClassName = '',
  resizerId = '',
  onResizePointerDown,
  children,
}) {
  const resizer = resizerId && typeof onResizePointerDown === 'function'
    ? <PaneResizer id={resizerId} onPointerDown={onResizePointerDown} />
    : null;
  return (
    <Tag id={id} className={cx('panel-card', className)} style={style}>
      {resizer}
      <div id={contentId} className={cx('panel-card-content', contentClassName)}>
        {children}
      </div>
    </Tag>
  );
}
