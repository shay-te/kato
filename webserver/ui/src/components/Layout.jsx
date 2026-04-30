export default function Layout({ left, center, right, rightWidth }) {
  return (
    <div
      id="layout"
      style={{ '--right-pane-width': `${rightWidth}px` }}
    >
      {left}
      {center}
      {right}
    </div>
  );
}
