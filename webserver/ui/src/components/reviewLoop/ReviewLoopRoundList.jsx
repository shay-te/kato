import { useCallback, useEffect, useState } from 'react';
import ReviewLoopRound from './ReviewLoopRound.jsx';
import { loopExpansion, rememberLoopExpansion } from '../../utils/reviewLoopExpandMemory.js';

// Every round, OLDEST FIRST: a new round appends at the bottom, so nothing the
// operator is reading moves. The newest round opens itself once when it
// appears; every other open/closed choice is the operator's, is never undone
// by a new round arriving, and is remembered across reloads and tab switches
// (reviewLoopExpandMemory). Mounted per loop (keyed by its id), so one loop's
// choices can never be written under another's.
export default function ReviewLoopRoundList({ taskId, loop }) {
  const rounds = loop?.rounds || [];
  const loopId = loop?.loop_id || '';
  const newest = rounds.length ? rounds[rounds.length - 1].number : 0;
  const [expanded, setExpanded] = useState(() => loopExpansion(taskId, loopId).rounds);
  // ONE place that both applies and remembers, as the Files pane does.
  const applyExpanded = useCallback((next) => {
    setExpanded(next);
    rememberLoopExpansion(taskId, loopId, { rounds: next });
  }, [taskId, loopId]);
  useEffect(() => {
    if (!newest || newest in expanded) { return; }
    applyExpanded({ ...expanded, [newest]: true });
  }, [newest, expanded, applyExpanded]);
  if (rounds.length === 0) { return null; }
  function toggle(number) {
    applyExpanded({ ...expanded, [number]: !expanded[number] });
  }
  const items = rounds.map((round) => (
    <ReviewLoopRound
      key={round.number}
      taskId={taskId}
      loopId={loop.loop_id}
      round={round}
      expanded={!!expanded[round.number]}
      onToggle={toggle}
    />
  ));
  return <ol className="review-loop-rounds">{items}</ol>;
}
