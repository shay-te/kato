import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import ReviewLoopArtifact from './ReviewLoopArtifact.jsx';
import {
  countsText,
  findingDecision,
  findingLocation,
  isBlockingFinding,
  roundOutcomeLabel,
  testsReportText,
  testsTone,
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
  // A clean-room sweep: a reviewer told nothing of the fixes, confirming the
  // clean round before it.
  const sweepTag = round.sweep ? <span className="review-loop-round-tag">Clean-room</span> : null;
  const tests = round.tests ? <RoundTests tests={round.tests} /> : null;
  const body = expanded ? (
    <div className="review-loop-round-body">
      {tests}
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
        {sweepTag}
        <span className="review-loop-round-counts">{summary}</span>
        <span className="review-loop-round-outcome">{roundOutcomeLabel(round)}</span>
      </button>
      {body}
    </li>
  );
}

// What the main chat reported after running the tests on this round's tree.
function RoundTests({ tests }) {
  const tone = testsTone(tests);
  const failures = (tests.failures || []).map((failure) => <li key={failure}>{failure}</li>);
  const failureList = failures.length ? <ul className="review-loop-tests-failures">{failures}</ul> : null;
  return (
    <section className={cx('review-loop-tests', `is-${tone}`)}>
      <p className="review-loop-tests-summary">{testsReportText(tests)}</p>
      {failureList}
    </section>
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
  const diffLabel = `Diff the reviewer saw (${round.diff_files} file(s) in ${round.diff_repos} repo(s))`;
  // Failing tests sent back carry no per-finding answers, so no reply file.
  const answeredFindings = round.fixed_at && round.outcome !== 'tests_failed';
  const shown = [
    { kind: 'review', label: 'Reviewer’s full report', when: round.reviewed_at },
    { kind: 'prompt', label: 'Message sent to the chat', when: round.sent_at },
    { kind: 'response', label: 'The chat’s reply to the findings', when: answeredFindings },
    { kind: 'tests', label: 'The chat’s test report', when: round.tests },
    { kind: 'diff', label: diffLabel, when: round.diff_files },
  ].filter((artifact) => artifact.when);
  const artifacts = shown.map(({ kind, label }) => (
    <ReviewLoopArtifact key={kind} taskId={taskId} loopId={loopId} round={round.number} kind={kind} label={label} />
  ));
  return <div className="review-loop-artifacts">{artifacts}</div>;
}
