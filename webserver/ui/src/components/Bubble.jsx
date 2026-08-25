import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { useAgentName } from '../contexts/AgentNameContext.jsx';

const KIND_LABELS = {
  [BUBBLE_KIND.USER]: 'You',
  // ASSISTANT is deliberately absent: it is the AGENT'S NAME, which depends
  // on the tab, and a constant here labelled Codex's replies "CLAUDE".
  [BUBBLE_KIND.TOOL]: 'Tool',
  [BUBBLE_KIND.SYSTEM]: 'System',
  [BUBBLE_KIND.ERROR]: 'Error',
};

// ``agentName`` names the assistant. Defaulted rather than required so the
// non-chat surfaces that render bubbles keep working; every chat bubble is
// given the real name by EventLog.
export default function Bubble({ kind, children, agentName = '' }) {
  const contextName = useAgentName();
  const label = kind === BUBBLE_KIND.ASSISTANT
    ? (agentName || contextName)
    : (KIND_LABELS[kind] || kind);
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
