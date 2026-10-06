import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import ReviewLoopArtifact from './ReviewLoopArtifact.jsx';
import {
  countsText,
  findingDecision,
  findingLocation,
  isBlockingFinding,
  roundOutcomeLabel,
} from './reviewLoopHelpers.js';

// One round of the loop: what the review found, what the chat decided about
// each finding it was sent (fixed + test, or rejected / out of scope + the
// evidence), and the round's saved texts.
export default function ReviewLoopRound({ taskId, loopId, round, expanded, onToggle }) {
  const blocking = round.findings.filter(isBlockingFinding);
  const settled = round.findings.filter((finding) => finding.settled_by);
  const others = round.findings.filter((finding) => !isBlockingFinding(finding) && !finding.settled_by);
  const blockingTitle = round.sent_at ? 'Blocking — sent to the chat to fix' : 'Blocking';
  const summary = round.reviewed_at ? countsText(round.counts) : 'not reviewed yet';
  const chevron = expanded ? 'chevron-down' : 'chevron-right';
  const body = expanded ? (
    <div className="review-loop-round-body">
      <FindingList title={blockingTitle} findings={blocking} round={round} />
      <FindingList title="Settled earlier — not sent again" findings={settled} round={round} />
      <FindingList title="Not blocking — reported only" findings={others} round={round} />
      <RoundArtifacts taskId={taskId} loopId={loopId} round={round} />
    </div>
  ) : null;
  return (
    <li className={cx('review-loop-round', `is-${round.outcome || 'active'}`)} data-round={round.number}>
      <button
        type="button"
        className="review-loop-round-header"
        aria-expanded={expanded}
        onClick={() => onToggle(round.number)}
      >
        <Icon name={chevron} />
        <span className="review-loop-round-name">Round {round.number}</span>
        <span className="review-loop-round-counts">{summary}</span>
        <span className="review-loop-round-outcome">{roundOutcomeLabel(round)}</span>
      </button>
      {body}
    </li>
  );
}

function FindingList({ title, findings, round }) {
  if (findings.length === 0) { return null; }
  const items = findings.map((finding, index) => (
    <FindingItem key={finding.id || `${finding.file}:${finding.line}:${index}`} finding={finding} round={round} />
  ));
  return (
    <section className="review-loop-findings">
      <h4>{title}</h4>
      <ul>{items}</ul>
    </section>
  );
}

function FindingItem({ finding, round }) {
  const id = finding.id ? <span className="review-loop-finding-id">{finding.id}</span> : null;
  const detail = finding.detail ? <p className="review-loop-finding-note">{finding.detail}</p> : null;
  const invariant = finding.invariant ? (
    <p className="review-loop-finding-note"><b>Invariant:</b> {finding.invariant}</p>
  ) : null;
  const newEvidence = finding.new_evidence ? (
    <p className="review-loop-finding-note"><b>Raised again with new evidence:</b> {finding.new_evidence}</p>
  ) : null;
  const settledBy = finding.settled_by ? (
    <p className="review-loop-finding-note">Settled by the decision on {finding.settled_by}.</p>
  ) : null;
  const decision = findingDecision(round, finding);
  const decisionLine = decision ? (
    <p className={cx('review-loop-decision', `is-${decision.tone}`)}>
      <b>{decision.label}</b>
      <span>{decision.note}</span>
    </p>
  ) : null;
  return (
    <li className="review-loop-finding">
      {id}
      <span className={cx('review-loop-severity', `is-${finding.severity.toLowerCase()}`)}>
        {finding.severity}
      </span>
      <span className="review-loop-finding-title">{finding.title}</span>
      <span className="review-loop-finding-location">{findingLocation(finding)}</span>
      {detail}
      {invariant}
      {newEvidence}
      {settledBy}
      {decisionLine}
    </li>
  );
}

function RoundArtifacts({ taskId, loopId, round }) {
  const sent = round.sent_at ? (
    <ReviewLoopArtifact taskId={taskId} loopId={loopId} round={round.number} kind="prompt" label="Message sent to the chat" />
  ) : null;
  const answered = round.fixed_at ? (
    <ReviewLoopArtifact taskId={taskId} loopId={loopId} round={round.number} kind="response" label="The chat’s reply to the findings" />
  ) : null;
  const reviewed = round.reviewed_at ? (
    <ReviewLoopArtifact taskId={taskId} loopId={loopId} round={round.number} kind="review" label="Reviewer’s full report" />
  ) : null;
  const diff = round.diff_files ? (
    <ReviewLoopArtifact
      taskId={taskId}
      loopId={loopId}
      round={round.number}
      kind="diff"
      label={`Diff the reviewer saw (${round.diff_files} file(s) in ${round.diff_repos} repo(s))`}
    />
  ) : null;
  return (
    <div className="review-loop-artifacts">
      {reviewed}
      {sent}
      {answered}
      {diff}
    </div>
  );
}
