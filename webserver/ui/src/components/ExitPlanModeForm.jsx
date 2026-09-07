import { useRef, useState } from 'react';
import { useAutoSizeTextarea } from '../hooks/useAutoSizeTextarea.js';
import MarkdownContent from './MarkdownContent.jsx';

// ``ExitPlanMode`` is not a permission to grant — it is the agent asking
// "shall I start building this?". The generic permission dialog rendered it as
// one: a tool name, "(no arguments)", a raw JSON envelope, and Deny / Allow
// once. Nothing there told the operator what they were approving or what
// either button would do.
//
// So it gets its own shape: the plan, then the two decisions in the operator's
// own words. Deliberately NOT a "remember this" surface — approving plan mode
// forever is exactly what must never be one click away (PermissionModal keeps
// ExitPlanMode in NEVER_REMEMBERED_TOOLS, and the backend refuses to
// auto-resolve it).
//
// The plan text is often absent from the envelope (``input: {}``): kato
// captures it from the ExitPlanMode event and writes ``plan.md``, which the
// centre pane renders. When it isn't here, say where it is rather than
// printing "(no arguments)".
export default function ExitPlanModeForm({
  plan = '',
  agentName = 'The agent',
  onApprove,
  onKeepPlanning,
}) {
  const [feedback, setFeedback] = useState('');
  const feedbackRef = useRef(null);
  useAutoSizeTextarea(feedbackRef, feedback);
  const planText = String(plan || '').trim();

  return (
    <div className="exit-plan">
      <p className="exit-plan-lead">
        <strong>{agentName}</strong> has finished planning and wants to start
        making changes.
      </p>
      <div className="exit-plan-scroll">
        {planText ? (
          <div className="exit-plan-body">
            <MarkdownContent>{planText}</MarkdownContent>
          </div>
        ) : (
          <p className="exit-plan-empty">
            The plan is open in the centre pane (the <strong>Plan</strong>
            {' '}view) — kato saves it to <code>plan.md</code> as the agent
            writes it. Read it there, then choose below.
          </p>
        )}
      </div>
      <label className="exit-plan-feedback">
        <span className="exit-plan-feedback-label">
          Feedback (sent only if you keep planning)
        </span>
        <textarea
          ref={feedbackRef}
          className="exit-plan-feedback-input"
          placeholder="What should change about the plan?"
          rows={2}
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
        />
      </label>
      <div className="modal-actions">
        <button
          type="button"
          className="secondary"
          onClick={() => onKeepPlanning(feedback.trim())}
        >
          Keep planning
        </button>
        <button type="button" className="primary" onClick={onApprove}>
          Start implementing
        </button>
      </div>
    </div>
  );
}
