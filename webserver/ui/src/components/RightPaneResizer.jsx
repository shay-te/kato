export default function RightPaneResizer({ onPointerDown }) {
  return (
    <div
      id="right-pane-resizer"
      onMouseDown={onPointerDown}
      title="Drag to resize"
    />
  );
}
