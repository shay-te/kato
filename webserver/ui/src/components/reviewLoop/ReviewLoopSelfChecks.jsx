import ReviewLoopArtifact from './ReviewLoopArtifact.jsx';
import { selfCheckText } from './reviewLoopHelpers.js';

// The main chat's own review of its change, before any independent reviewer:
// one line per turn ("Self-check 1 — fixed 2"), what it said it did, and its
// full reply on demand. Renders nothing for a loop that ran no self-check.
export default function ReviewLoopSelfChecks({ taskId, loop }) {
  const turns = loop?.self_checks || [];
  if (turns.length === 0) { return null; }
  const items = turns.map((turn) => {
    const summary = turn.summary ? <p className="review-loop-self-check-summary">{turn.summary}</p> : null;
    const reply = turn.finished_at ? (
      <ReviewLoopArtifact
        taskId={taskId}
        loopId={loop.loop_id}
        round={turn.number}
        kind="self_check"
        label="The chat’s self-check reply"
      />
    ) : null;
    return (
      <li key={turn.number} className="review-loop-self-check">
        <span className="review-loop-self-check-name">{selfCheckText(turn)}</span>
        {summary}
        {reply}
      </li>
    );
  });
  return (
    <section className="review-loop-self-checks">
      <h4>Self-check in the main chat</h4>
      <ul>{items}</ul>
    </section>
  );
}
