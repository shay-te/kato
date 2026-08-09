// Tiny draggable boundary div, rendered for a panel by ``PanelCard``.
// The ``id`` is load-bearing for CSS selectors (#left-pane-resizer sits
// on its card's right edge, #right-pane-resizer on its card's left one),
// so callers pass it explicitly.
export default function PaneResizer({ id, onPointerDown }) {
  return (
    <div
      id={id}
      className="pane-resizer"
      onMouseDown={onPointerDown}
      title="Drag to resize"
    >
      {/* Always-visible dot grip. Without it the gutter between two
          panel cards reads as dead space and the operator has no cue
          that the boundary can be dragged at all. */}
      <span className="pane-resizer-grip" aria-hidden="true" />
    </div>
  );
}
