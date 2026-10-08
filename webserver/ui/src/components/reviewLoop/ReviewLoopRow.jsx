import Icon from '../Icon.jsx';

// One collapsible row of the review loop view — a round, or one of the main
// chat's self-check turns. The header always shows what it is, what it found,
// how long it took (once finished) and its outcome; open, it shows ``children``.
export default function ReviewLoopRow({
  className, name, tag = null, summary, timing = null, outcome, expanded, onToggle, children, ...rest
}) {
  const chevron = expanded ? 'chevron-down' : 'chevron-right';
  const duration = timing ? (
    <span className="review-loop-round-duration" title={timing.title}>{timing.text}</span>
  ) : null;
  const body = expanded ? <div className="review-loop-round-body">{children}</div> : null;
  return (
    <li className={className} {...rest}>
      <button type="button" className="review-loop-round-header" aria-expanded={expanded} onClick={onToggle}>
        <Icon name={chevron} />
        <span className="review-loop-round-name">{name}</span>
        {tag}
        <span className="review-loop-round-counts">{summary}</span>
        {duration}
        <span className="review-loop-round-outcome">{outcome}</span>
      </button>
      {body}
    </li>
  );
}
