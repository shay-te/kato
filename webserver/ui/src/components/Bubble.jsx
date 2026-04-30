export default function Bubble({ kind, children }) {
  return <div className={`bubble ${kind}`}>{children}</div>;
}
