import { useEffect, useState } from 'react';
import ReviewLoopRound from './ReviewLoopRound.jsx';

// Every round, OLDEST FIRST: a new round appends at the bottom, so nothing the
// operator is reading moves. The newest round opens itself once when it
// appears; every other open/closed choice is the operator's and is never
// undone by a new round arriving.
export default function ReviewLoopRoundList({ taskId, loop }) {
  const rounds = loop?.rounds || [];
  const newest = rounds.length ? rounds[rounds.length - 1].number : 0;
  const [expanded, setExpanded] = useState({});
  useEffect(() => {
    if (!newest) { return; }
    setExpanded((previous) => (newest in previous ? previous : { ...previous, [newest]: true }));
  }, [newest]);
  if (rounds.length === 0) { return null; }
  function toggle(number) {
    setExpanded((previous) => ({ ...previous, [number]: !previous[number] }));
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
