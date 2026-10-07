// The review loop's wording and "where is it now" model, on real summaries
// shaped exactly like ``session.review_loop`` from the backend.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  blockingCount,
  countsText,
  findingDecision,
  findingLocation,
  formatElapsed,
  isBlockingFinding,
  isReviewLoopRunning,
  reviewLoopChip,
  reviewLoopRoundsText,
  reviewLoopTabEntries,
  buildReviewLoopTabModels,
  reviewLoopSentence,
  reviewLoopSignature,
  reviewLoopSteps,
  roundOutcomeLabel,
  selfCheckText,
  testsReportText,
} from './reviewLoopHelpers.js';

const NOW = 10_000;

function loop(overrides = {}) {
  return {
    loop_id: 'a'.repeat(32),
    status: 'running',
    phase: 'awaiting_fix',
    round: 2,
    max_rounds: 5,
    started_at: NOW - 600,
    phase_started_at: NOW - 252,
    finished_at: 0,
    waiting_for: '',
    reason: '',
    counts: { BLOCKER: 1, MAJOR: 2, MINOR: 1, NIT: 3 },
    ...overrides,
  };
}

test('elapsed reads like a person would say it', () => {
  assert.equal(formatElapsed(45), '45s');
  assert.equal(formatElapsed(252), '4m 12s');
  assert.equal(formatElapsed(3780), '1h 3m');
  assert.equal(formatElapsed(-5), '0s');
});

test('counts: blocking first, zeros left out, an empty review says so', () => {
  assert.equal(blockingCount({ BLOCKER: 1, MAJOR: 2 }), 3);
  assert.equal(countsText({ BLOCKER: 1, MAJOR: 2, MINOR: 1, NIT: 3 }), '3 blocking · 1 minor · 3 nit');
  assert.equal(countsText({ BLOCKER: 0, MAJOR: 0, MINOR: 0, NIT: 0 }), 'no issues');
  assert.equal(countsText(null), '');
  assert.equal(blockingCount(null), 0);
});

test('the chip says where a running loop is, and for how long', () => {
  assert.deepEqual(reviewLoopChip(loop(), NOW), {
    text: '2/5 · fixing · 4m 12s', tone: 'running', running: true,
  });
  assert.equal(reviewLoopChip(loop({ phase: 'reviewing' }), NOW).text, '2/5 · reviewing · 4m 12s');
  assert.equal(reviewLoopChip(loop({ phase: 'waiting_to_send' }), NOW).text, '2/5 · findings ready · 4m 12s');
  assert.equal(reviewLoopChip(null, NOW), null);
});

test('the chip names how a finished loop ended', () => {
  assert.deepEqual(reviewLoopChip(loop({ status: 'clean', phase: 'done' }), NOW),
    { text: 'clean', tone: 'good', running: false });
  assert.equal(reviewLoopChip(loop({ status: 'stuck' }), NOW).tone, 'warn');
  assert.equal(reviewLoopChip(loop({ status: 'max_rounds' }), NOW).tone, 'warn');
  assert.equal(reviewLoopChip(loop({ status: 'failed' }), NOW).tone, 'bad');
  assert.equal(reviewLoopChip(loop({ status: 'something-new' }), NOW), null);
});

test('one sentence for tooltips: position while running, outcome and reason after', () => {
  assert.equal(
    reviewLoopSentence(loop({ waiting_for: 'the agent is mid-turn', phase: 'waiting_to_send' }), NOW),
    'Round 2 of 5 · findings ready — they go to the chat when it is free (the agent is mid-turn) · 4m 12s',
  );
  assert.equal(
    reviewLoopSentence(loop({ status: 'clean', round: 2, reason: 'round 2 found no blocking issues' }), NOW),
    'The review loop finished clean after 2 rounds — round 2 found no blocking issues',
  );
  assert.equal(reviewLoopSentence(loop({ status: 'stopped', round: 1, reason: '' }), NOW),
    'The review loop was stopped after 1 round');
  assert.equal(reviewLoopSentence(null, NOW), '');
});

test('the tracker marks done, current and next steps for the current round', () => {
  const steps = reviewLoopSteps(loop(), NOW);
  assert.deepEqual(steps.map((s) => [s.key, s.state, s.label]), [
    ['review', 'done', 'Reviewed'],
    ['send', 'done', 'Sent to the chat'],
    ['fix', 'current', 'Fixing in the main chat'],
    ['next', 'todo', 'Review round 3'],
  ]);
  assert.equal(steps[0].detail, '3 blocking · 1 minor · 3 nit');
  assert.equal(steps[2].detail, '4m 12s');
});

test('the tracker says what a waiting step is waiting for', () => {
  const steps = reviewLoopSteps(loop({ phase: 'waiting_to_review', round: 1, waiting_for: 'a diff comment goes first' }), NOW);
  assert.deepEqual(steps.map((s) => s.state), ['current', 'todo', 'todo', 'todo']);
  assert.equal(steps[0].label, 'Waiting to review');
  assert.equal(steps[0].detail, 'a diff comment goes first · 4m 12s');
  const reviewing = reviewLoopSteps(loop({ phase: 'reviewing' }), NOW);
  assert.equal(reviewing[0].label, 'Reviewing the whole change');
  assert.equal(reviewing[1].label, 'Send the findings to the chat');
});

test('the last round leads to the end of the loop, not another review', () => {
  const steps = reviewLoopSteps(loop({ round: 5 }), NOW);
  assert.equal(steps[3].label, 'Loop ends');
});

test('a finished loop has no tracker', () => {
  assert.deepEqual(reviewLoopSteps(loop({ status: 'clean' }), NOW), []);
  assert.deepEqual(reviewLoopSteps(null, NOW), []);
  assert.equal(isReviewLoopRunning(loop({ status: 'clean' })), false);
});

test('a finding is located the way the reviewer reported it', () => {
  assert.equal(findingLocation({ repository: 'api', file: 'app.py', line: 42, symbol: 'run' }), 'api/app.py:42 (run)');
  assert.equal(findingLocation({ repository: '', file: 'app.py', line: 0, symbol: '' }), 'app.py');
  assert.equal(findingLocation({ repository: '', file: '', line: 3, symbol: 'x' }), ' (x)');
});

test('round outcomes in words', () => {
  assert.equal(roundOutcomeLabel({ outcome: 'sent' }), 'sent to the chat');
  assert.equal(roundOutcomeLabel({ outcome: 'stuck' }), 'same issues as before');
  assert.equal(roundOutcomeLabel({ outcome: '' }), 'in progress');
});

test('the signature moves only when the loop takes a new step', () => {
  const base = reviewLoopSignature(loop());
  assert.equal(reviewLoopSignature(loop({ phase_started_at: NOW, waiting_for: 'x' })), base);
  assert.notEqual(reviewLoopSignature(loop({ phase: 'reviewing' })), base);
  assert.notEqual(reviewLoopSignature(loop({ round: 3 })), base);
  assert.notEqual(reviewLoopSignature(loop({ status: 'clean' })), base);
  assert.equal(reviewLoopSignature(null), '');
});

test('the round-limit choices read as words', () => {
  assert.equal(reviewLoopRoundsText(1), '1 round');
  assert.equal(reviewLoopRoundsText(5), '5 rounds');
});

test('a settled finding is not blocking and is counted apart', () => {
  assert.equal(isBlockingFinding({ severity: 'MAJOR' }), true);
  assert.equal(isBlockingFinding({ severity: 'BLOCKER', settled_by: 'R1-2' }), false);
  assert.equal(isBlockingFinding({ severity: 'MINOR' }), false);
  assert.equal(isBlockingFinding(null), false);
  assert.equal(countsText({ BLOCKER: 0, MAJOR: 1, MINOR: 0, NIT: 0, SETTLED: 2 }), '1 blocking · 2 settled');
  assert.equal(countsText({ BLOCKER: 0, MAJOR: 0, MINOR: 0, NIT: 0, SETTLED: 0 }), 'no issues');
});

test('what the chat decided about a finding, in words', () => {
  const round = { responses: [
    { finding_id: 'R1-1', decision: 'fixed', test: 't::a', evidence: '' },
    { finding_id: 'R1-2', decision: 'fixed', test: '', evidence: 'changed it' },
    { finding_id: 'R1-3', decision: 'rejected', test: '', evidence: 'see go source' },
    { finding_id: 'R1-4', decision: 'out_of_scope', test: '', evidence: '' },
    { finding_id: 'R1-5', decision: 'something new', test: '', evidence: '' },
  ] };
  const decide = (id) => findingDecision(round, { id });
  assert.deepEqual(decide('R1-1'), { label: 'Fixed', tone: 'good', note: 'test: t::a' });
  assert.deepEqual(decide('R1-2'), { label: 'Fixed', tone: 'good', note: 'no test named · changed it' });
  assert.deepEqual(decide('R1-3'), { label: 'Rejected', tone: 'warn', note: 'see go source' });
  assert.deepEqual(decide('R1-4'), { label: 'Out of scope', tone: 'neutral', note: 'no evidence given, so not settled' });
  assert.deepEqual(decide('R1-5'), { label: 'Not answered', tone: 'bad', note: '' });
  assert.equal(decide('R1-9'), null);
  assert.equal(findingDecision({}, { id: 'R1-1' }), null);
});


test('loop-tab entries carry each task\'s state colour', () => {
  const loopFor = (id) => ({
    'UNA-1': { status: 'running', phase: 'reviewing', round: 1, max_rounds: 3 },
    'UNA-2': { status: 'clean', phase: 'done', round: 2, max_rounds: 3 },
  }[id] || null);
  assert.deepEqual(reviewLoopTabEntries(['UNA-1', 'UNA-2', 'UNA-3'], loopFor), [
    { taskId: 'UNA-1', tone: 'running' },
    { taskId: 'UNA-2', tone: 'good' },
    { taskId: 'UNA-3', tone: 'idle' },
  ]);
  assert.deepEqual(reviewLoopTabEntries(null, loopFor), []);
});

test('loop-tab models mark the active tab and the group divider', () => {
  const tabs = [{ taskId: 'UNA-1', tone: 'running' }, { taskId: 'UNA-2', tone: 'good' }];
  const withFiles = buildReviewLoopTabModels(tabs, { activeTaskId: 'UNA-2', hasFileTabs: true });
  assert.deepEqual(withFiles, [
    { taskId: 'UNA-1', tone: 'running', active: false, groupStart: true },
    { taskId: 'UNA-2', tone: 'good', active: true, groupStart: false },
  ]);
  // Leading the strip (no file tabs) → no divider on the first loop tab.
  assert.equal(buildReviewLoopTabModels(tabs, { hasFileTabs: false })[0].groupStart, false);
  assert.deepEqual(buildReviewLoopTabModels(null), []);
});

test('the self-check and the test run have their own short tracks', () => {
  const base = { status: 'running', round: 1, max_rounds: 5, phase_started_at: NOW - 30 };
  const self = reviewLoopSteps({ ...base, phase: 'self_check', self_check_turn: 2 }, NOW);
  assert.deepEqual(self.map((s) => [s.label, s.state]), [
    ['Self-check in the main chat (turn 2)', 'current'],
    ['Independent review', 'todo'],
  ]);
  assert.equal(self[0].detail, '30s');

  const testing = reviewLoopSteps({
    ...base, phase: 'verifying', counts: { BLOCKER: 0, MAJOR: 0, MINOR: 1, NIT: 0 },
    extra_sweep: true, waiting_for: 'the agent is mid-turn',
  }, NOW);
  assert.deepEqual(testing.map((s) => [s.label, s.state]), [
    ['Reviewed — no blocking issues', 'done'],
    ['Running the tests in the main chat', 'current'],
    ['Clean-room check', 'todo'],
  ]);
  assert.equal(testing[0].detail, '1 minor');
  assert.equal(testing[1].detail, 'the agent is mid-turn · 30s');
  const noSweep = reviewLoopSteps({ ...base, phase: 'verifying', counts: null }, NOW);
  assert.equal(noSweep[2].label, 'Loop ends');
});

test('a clean-room sweep says so while it reviews', () => {
  const steps = reviewLoopSteps(
    { status: 'running', phase: 'reviewing', round: 3, max_rounds: 5, sweep: true }, NOW,
  );
  assert.equal(steps[0].label, 'Clean-room check');
  assert.equal(roundOutcomeLabel({ outcome: 'tests_failed' }), 'tests failed — sent back');
  assert.equal(roundOutcomeLabel({ outcome: 'changed' }), 'code changed since — not accepted');
});

test('test reports and self-check turns read as words', () => {
  assert.equal(testsReportText(null), '');
  assert.equal(testsReportText({ passed: true, summary: '41 passed', command: 'pytest' }),
    'Tests passed — 41 passed (pytest)');
  assert.equal(testsReportText({ passed: true }), 'Tests passed');
  assert.equal(testsReportText({ passed: false, failures: ['a', 'b'] }), 'Tests failed — 2 failing');
  assert.equal(testsReportText({ passed: false, failures: [], summary: 'crashed' }), 'Tests failed — crashed');
  assert.equal(testsReportText({ passed: false }), 'Tests failed — see the report');
  assert.equal(testsReportText({ passed: null }), 'Tests not reported');

  assert.equal(selfCheckText({ number: 1, clean: false, fixed: 2 }), 'Self-check 1 — fixed 2');
  assert.equal(selfCheckText({ number: 2, clean: true }), 'Self-check 2 — clean');
  assert.equal(selfCheckText({ number: 1, clean: null }), 'Self-check 1 — no verdict');
});

