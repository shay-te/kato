import { useRef, useState } from 'react';
import { fetchSessionPlan } from '../api.js';
import { useAutoSizeTextarea } from '../hooks/useAutoSizeTextarea.js';
import { usePolling } from '../hooks/usePolling.js';
import MarkdownContent from './MarkdownContent.jsx';

// ``ExitPlanMode`` is not a permission to grant — it is the agent asking
// "shall I start building this?". The generic permission dialog rendered it as
// one: a tool name, "(no arguments)", a raw JSON envelope, and Deny / Allow
// once. Nothing there told the operator what they were approving or what
// either button would do.
//
// So it gets its own shape: the plan, then the decisions in the operator's own
// words. Deliberately NOT a "remember this" surface — approving plan mode
// forever is exactly what must never be one click away (PermissionModal keeps
// ExitPlanMode in NEVER_REMEMBERED_TOOLS, and the backend refuses to
// auto-resolve it).
//
// THE PLAN IS SHOWN HERE, whether or not the envelope carries it. The CLI
// often calls the tool with ``input: {}`` and leaves the plan in the turn's
// message, and this dialog used to answer that by pointing at the centre pane
// — a pane it covers, with no way to get there and no way to stop the agent:
// "there is no way to check his plan… it never shows me the button to see the
// plan and stop the claude". kato captures the plan out of the event stream
// and writes ``plan.md`` within a tick of the ask, so the dialog reads it from
// there instead of sending anyone anywhere.
//
// Stop is the third decision, and it was missing. "Keep planning" hands the
// agent feedback and it plans on; the operator who wants to read, think, or
// take over needs the session to actually stop.

// The plan lands in ``plan.md`` a beat after the ask (the capture watcher
// polls the session). Re-read until it does.
const PLAN_RETRY_MS = 3000;

export default function ExitPlanModeForm({
  plan = '',
  taskId = '',
  agentName = 'The agent',
  onApprove,
  onKeepPlanning,
  onStop = null,
}) {
  const [feedback, setFeedback] = useState('');
  const [capturedPlan, setCapturedPlan] = useState('');
  const feedbackRef = useRef(null);
  useAutoSizeTextarea(feedbackRef, feedback);
  const planText = String(plan || '').trim();

  usePolling(async () => {
    const result = await fetchSessionPlan(taskId);
    setCapturedPlan(String(result?.content || '').trim());
  }, PLAN_RETRY_MS, [taskId], { enabled: !planText && !!taskId });

  const shownPlan = planText || capturedPlan;

  return (
    <div className="exit-plan">
      <p className="exit-plan-lead">
        <strong>{agentName}</strong> has finished planning and wants to start
        making changes.
      </p>
      <div className="exit-plan-scroll">
        {shownPlan ? (
          <div className="exit-plan-body">
            <MarkdownContent>{shownPlan}</MarkdownContent>
          </div>
        ) : (
          <p className="exit-plan-empty">
            {agentName} sent no plan text with the request. Kato is reading it
            out of the conversation — it appears here the moment it lands, and
            is saved to <code>plan.md</code>. If it never does, the plan is the
            agent&apos;s last message in the chat: stop the agent to read it
            there.
          </p>
        )}
      </div>
      {!planText && capturedPlan && (
        <p className="exit-plan-source">
          Read from <code>plan.md</code> — what kato captured from this turn.
        </p>
      )}
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
        {typeof onStop === 'function' && (
          <button
            type="button"
            className="secondary exit-plan-stop tooltip-above tooltip-start"
            data-tooltip={`Answer this ask and stop ${agentName}. The chat and its history are kept — your next message resumes the session.`}
            onClick={onStop}
          >
            Stop
          </button>
        )}
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
