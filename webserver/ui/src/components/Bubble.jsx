import { BUBBLE_KIND } from '../constants/bubbleKind.js';

const KIND_LABELS = {
  [BUBBLE_KIND.USER]: 'You',
  [BUBBLE_KIND.ASSISTANT]: 'Claude',
  [BUBBLE_KIND.TOOL]: 'Tool',
  [BUBBLE_KIND.SYSTEM]: 'System',
  [BUBBLE_KIND.ERROR]: 'Error',
};

export default function Bubble({ kind, children }) {
  const label = KIND_LABELS[kind] || kind;
  const className = `bubble ${kind}`;
  return (
    <div className={className}>
      <span className="bubble-dot" aria-hidden="true" />
      <div className="bubble-body">
        <div className="bubble-label">{label}</div>
        <div className="bubble-content">{children}</div>
      </div>
    </div>
  );
}
