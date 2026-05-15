export default function LeftPaneResizer({ onPointerDown }) {
  return (
    <div
      id="left-pane-resizer"
      onMouseDown={onPointerDown}
      title="Drag to resize"
    />
  );
}
