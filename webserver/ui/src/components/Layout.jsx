export default function Layout({ top, left, center, right, rightWidth }) {
  return (
    <div
      id="layout"
      className={top ? 'has-top-tabs' : ''}
      style={{ '--right-pane-width': `${rightWidth}px` }}
    >
      {top}
      {left}
      {center}
      {right}
    </div>
  );
}
