import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import { loopExpansion, rememberLoopExpansion } from '../../utils/reviewLoopExpandMemory.js';
import ReviewLoopArtifact from './ReviewLoopArtifact.jsx';
import ReviewLoopRow from './ReviewLoopRow.jsx';
import { selfCheckRow } from './reviewLoopHelpers.js';

// The main chat's own review of its change, before any independent reviewer:
// one row per turn, like a round — what it fixed, how long it took, its
// outcome — collapsed until opened. By the time the rounds run, what each
// turn fixed is history; it stays one click away, never in the way. What
// was opened is remembered like the rounds (reviewLoopExpandMemory).
// Renders nothing for a loop that ran no self-check.
export default function ReviewLoopSelfChecks({ taskId, loop }) {
  const turns = loop?.self_checks || [];
  const loopId = loop?.loop_id || '';
  const [open, setOpen] = useState(() => loopExpansion(taskId, loopId).selfChecks);
  if (turns.length === 0) { return null; }
  const toggle = (number) => {
    const next = { ...open, [number]: !open[number] };
    setOpen(next);
    rememberLoopExpansion(taskId, loopId, { selfChecks: next });
  };
  const items = turns.map((turn) => (
    <SelfCheckTurn
      key={turn.number}
      taskId={taskId}
      loopId={loop.loop_id}
      turn={turn}
      expanded={!!open[turn.number]}
      onToggle={toggle}
    />
  ));
  return (
    <section className="review-loop-self-checks">
      <h4>Self-check in the main chat</h4>
      <ol>{items}</ol>
    </section>
  );
}

function SelfCheckTurn({ taskId, loopId, turn, expanded, onToggle }) {
  const row = selfCheckRow(turn);
  const summary = turn.summary ? <p className="review-loop-self-check-summary">{turn.summary}</p> : null;
  const reply = turn.finished_at ? (
    <ReviewLoopArtifact
      taskId={taskId}
      loopId={loopId}
      round={turn.number}
      kind="self_check"
      label="The chat’s self-check reply"
    />
  ) : null;
  return (
    <ReviewLoopRow
      className={cx('review-loop-round', 'is-self-check', `is-${row.state}`)}
      data-self-check={turn.number}
      name={row.name}
      summary={row.summary}
      timing={row.timing}
      outcome={row.outcome}
      expanded={expanded}
      onToggle={() => onToggle(turn.number)}
    >
      {summary}
      {reply}
    </ReviewLoopRow>
  );
}
