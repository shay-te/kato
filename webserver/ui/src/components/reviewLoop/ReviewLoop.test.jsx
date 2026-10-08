// The review loop's UI, rendered for real. The ONE stand-in is the network:
// ``fetch`` answers from an in-memory kato that serves the review-loop routes
// with the same JSON shapes the backend returns — everything from the hook
// down to the DOM is the real code.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import EventLog from '../EventLog.jsx';
import ReviewLoopButton from './ReviewLoopButton.jsx';
import ReviewLoopChip from './ReviewLoopChip.jsx';
import ReviewLoopPane from './ReviewLoopPane.jsx';
import ReviewLoopTabBadge from './ReviewLoopTabBadge.jsx';
import {
  markReviewLoopSeen,
  readReviewLoopTabs,
  reviewLoopView,
  writeReviewLoopTabs,
} from './reviewLoopViewStore.js';
import { _resetReviewLoopRounds, readReviewLoopRounds } from './reviewLoopRoundsPref.js';
import { _resetReviewLoopStages, readReviewLoopStages } from './reviewLoopStagesPref.js';
import { _resetReviewLoopModel, readReviewLoopModel, writeReviewLoopModel } from './reviewLoopModelPref.js';
import { toastStore } from '../../stores/toastStore.js';

const ROUNDS_KEY = 'kato.reviewLoopRounds.v1';
const STAGES_KEY = 'kato.reviewLoopStages.v2';
// The self-check is off until ticked; the other stages are on until unticked.
const DEFAULT_STAGES = {
  self_check: false, verify_tests: true, confirm_clean: true, extra_sweep: true,
};

const LOOP_ID = 'b'.repeat(32);
const NOW = Date.now() / 1000;

function summary(overrides = {}) {
  return {
    loop_id: LOOP_ID, status: 'running', phase: 'awaiting_fix', round: 2, max_rounds: 5,
    started_at: NOW - 600, phase_started_at: NOW - 30, finished_at: 0,
    waiting_for: '', reason: '', counts: { BLOCKER: 0, MAJOR: 1, MINOR: 1, NIT: 0 },
    ...overrides,
  };
}

function round(number, overrides = {}) {
  return {
    number, started_at: NOW - 500, reviewed_at: NOW - 400, sent_at: NOW - 300, fixed_at: 0,
    findings: [
      { severity: 'MAJOR', title: `bug in round ${number}`, file: 'app.py', repository: 'api', line: 7, symbol: 'run', category: 'correctness', detail: 'return the right value' },
      { severity: 'NIT', title: 'rename x', file: 'app.py', repository: 'api', line: 1, symbol: '', category: 'other', detail: '' },
    ],
    counts: { BLOCKER: 0, MAJOR: 1, MINOR: 0, NIT: 1 },
    diff_repos: 1, diff_files: 2, diff_omitted: 0, outcome: 'sent',
    ...overrides,
  };
}

// An in-memory kato: the routes, their JSON, and a log of what was asked.
function fakeKato() {
  const kato = {
    loop: null, artifacts: {}, requests: [], startBodies: [],
    // The chat's model catalogue, and the reviewer's own default — pinned,
    // so not one of the catalogue's aliases.
    models: [{ id: 'opus', label: 'Opus 5.5', default: true }, { id: 'sonnet', label: 'Sonnet 5.5' }],
    defaultModel: { model: 'claude-opus-5-5[1m]', label: 'Opus 5.5 (1M context)' },
  };
  vi.stubGlobal('fetch', async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    kato.requests.push(`${method} ${url}`);
    const respond = (status, body) => ({ ok: status < 400, status, statusText: '', json: async () => body });
    const artifact = /\/review-loop\/([0-9a-f]+)\/rounds\/(\d+)\/(\w+)$/.exec(url);
    if (artifact) {
      const text = kato.artifacts[`${artifact[2]}/${artifact[3]}`];
      return text === undefined ? respond(404, { error: 'no such round or artifact' }) : respond(200, { text });
    }
    if (url.endsWith('/review-loop/stop')) { return respond(200, { stopped: true }); }
    if (url.endsWith('/review-loop/resume')) {
      if (!kato.loop?.resume) { return respond(409, { error: 'there is nothing to resume' }); }
      const resumed = summary({ resumes: 1, resume_note: kato.loop.resume, resume: '' });
      return respond(202, { loop: { ...resumed, rounds: kato.loop.rounds } });
    }
    if (url.endsWith('/review-loop') && method === 'POST') {
      const body = JSON.parse(init.body || '{}');
      kato.startBodies.push(body);
      const started = summary({ round: 1, phase: 'reviewing', max_rounds: body.max_rounds || 5 });
      return respond(202, { loop: { ...started, rounds: [] } });
    }
    if (url.endsWith('/review-loop')) { return respond(200, { loop: kato.loop }); }
    if (url === '/api/models') { return respond(200, { models: kato.models }); }
    if (url === '/api/review-loop/default-model') { return respond(200, kato.defaultModel); }
    return respond(404, { error: 'unknown route' });
  });
  return kato;
}

let kato;
beforeEach(() => {
  kato = fakeKato();
  window.localStorage.removeItem(ROUNDS_KEY);
  window.localStorage.removeItem(STAGES_KEY);
  _resetReviewLoopRounds();
  _resetReviewLoopStages();
  window.localStorage.removeItem('kato.reviewLoopModel.v1');
  _resetReviewLoopModel();
});
afterEach(() => { vi.unstubAllGlobals(); });

function session(loop) {
  return { task_id: 'UNA-1', task_summary: 'Audit log', review_loop: loop };
}

function openRequests() {
  const seen = [];
  const unsubscribe = reviewLoopView.subscribe((request) => { seen.push(request); });
  return { seen: () => seen.slice(1), unsubscribe };  // drop the replay on subscribe
}

describe('ReviewLoopPane — where the loop is and every round', () => {
  test('a running loop shows its live position and its rounds', async () => {
    kato.loop = { ...summary(), rounds: [round(1), round(2, { outcome: '', sent_at: 0, reviewed_at: NOW - 60 })] };
    render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);

    const tracker = screen.getByRole('list', { name: 'Round 2 of 5' });
    const current = within(tracker).getByText('Fixing in the main chat').closest('li');
    expect(current).toHaveClass('is-current');
    expect(within(tracker).getByText('Reviewed').closest('li')).toHaveClass('is-done');
    expect(within(tracker).getByText('Review round 3').closest('li')).toHaveClass('is-todo');

    await screen.findByText('Round 1');
    expect(screen.getByText('Round 2')).toBeInTheDocument();
    expect(kato.requests).toContain('GET /api/sessions/UNA-1/review-loop');
  });

  test('a finding a fix said it fixed, found again, is marked as still here', async () => {
    const repeated = round(3, {
      outcome: 'stuck',
      findings: [{
        id: 'R3-1', severity: 'MAJOR', title: 'empty list crashes average', file: 'app.py',
        repository: 'api', line: 7, symbol: 'average', category: 'correctness', detail: '',
        repeat_of: 'R2-1', missed_fixes: 2,
      }],
      counts: { BLOCKER: 0, MAJOR: 1, MINOR: 0, NIT: 0 },
    });
    const stuck = summary({ status: 'stuck', phase: 'done', reason: 'R2-1 still found after 2 fixes' });
    kato.loop = { ...stuck, rounds: [repeated] };
    render(<ReviewLoopPane session={session(stuck)} onClose={() => {}} />);
    const row = (await screen.findByText('Round 3')).closest('.review-loop-round');
    expect(within(row).getByText('stuck — fixes didn\u2019t land')).toBeInTheDocument();
    expect(within(row).getByText('Still here — first reported as R2-1, survived 2 fixes.'))
      .toHaveClass('is-repeat');
    expect(screen.getByText(/R2-1 still found after 2 fixes/)).toBeInTheDocument();
  });

  test('a finished round shows how long it took; the running one does not', async () => {
    kato.loop = {
      ...summary(),
      rounds: [
        round(1, { started_at: NOW - 900, reviewed_at: NOW - 648, sent_at: NOW - 640, fixed_at: NOW - 520, finished_at: NOW - 519 }),
        round(2, { outcome: '', sent_at: 0, reviewed_at: 0, fixed_at: 0, started_at: NOW - 60 }),
      ],
    };
    const { container } = render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    await screen.findByText('Round 1');
    const first = container.querySelector('[data-round="1"] .review-loop-round-duration');
    expect(first).toHaveTextContent('6m 21s');
    expect(first).toHaveAttribute('title', 'Round 1 took 6m 21s — review 4m 12s · fix 2m 0s');
    expect(container.querySelector('[data-round="2"] .review-loop-round-duration')).toBeNull();
  });

  test('the newest round opens itself; blocking and reported-only findings are apart', async () => {
    kato.loop = { ...summary(), rounds: [round(1), round(2)] };
    render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    await screen.findByText('bug in round 2');
    expect(screen.queryByText('bug in round 1')).not.toBeInTheDocument();
    expect(screen.getByText('Blocking — sent to the chat to fix')).toBeInTheDocument();
    expect(screen.getByText('Not blocking — reported only')).toBeInTheDocument();
    expect(screen.getByText('api/app.py:7 (run)')).toBeInTheDocument();
  });

  test('each sent finding shows what the chat decided, and settled repeats stand apart', async () => {
    const decided = round(1, {
      fixed_at: NOW - 200,
      findings: [
        { id: 'R1-1', severity: 'MAJOR', title: 'divide multiplies', file: 'calc.py', repository: 'api', line: 5, symbol: 'divide', category: 'correctness', detail: '', invariant: 'divide(a, b) == a / b' },
        { id: 'R1-2', severity: 'MAJOR', title: 'zip extra parsing differs from Go', file: 'zip.go', repository: 'api', line: 9, symbol: 'parse', category: 'correctness', detail: '' },
        { id: 'R1-3', severity: 'BLOCKER', title: 'no limit on entries', file: 'zip.go', repository: 'api', line: 2, symbol: 'open', category: 'security', detail: '' },
      ],
      responses: [
        { finding_id: 'R1-1', decision: 'fixed', test: 'tests/test_calc.py::test_divide', evidence: '', settles: false },
        { finding_id: 'R1-2', decision: 'rejected', test: '', evidence: 'Go reads extra.sub(fieldSize) first', settles: true },
        { finding_id: 'R1-3', decision: 'unanswered', test: '', evidence: '', settles: false },
      ],
    });
    const settledRepeat = round(2, {
      sent_at: 0, outcome: 'clean',
      findings: [{ id: 'R2-1', severity: 'MAJOR', title: 'zip extra parsing differs from Go', file: 'zip.go', repository: 'api', line: 9, symbol: 'parse', category: 'correctness', detail: '', settled_by: 'R1-2' }],
      counts: { BLOCKER: 0, MAJOR: 0, MINOR: 0, NIT: 0, SETTLED: 1 },
    });
    kato.loop = { ...summary({ status: 'clean', phase: 'done' }), rounds: [decided, settledRepeat] };
    kato.artifacts['1/response'] = 'Rejected R1-2: Go reads extra.sub first.';
    render(<ReviewLoopPane session={session(summary({ status: 'clean', phase: 'done' }))} onClose={() => {}} />);

    // The newest round: the repeat is settled, not blocking, and says by what.
    await screen.findByText('Settled earlier — not sent again');
    expect(screen.queryByText(/^Blocking/)).not.toBeInTheDocument();
    expect(screen.getByText('Settled by the decision on R1-2.')).toBeInTheDocument();
    expect(screen.getByText('1 settled')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Round 1'));
    const fixed = screen.getByText('divide multiplies').closest('li');
    expect(within(fixed).getByText('R1-1')).toBeInTheDocument();
    expect(fixed).toHaveTextContent('Invariant: divide(a, b) == a / b');
    expect(fixed.querySelector('.review-loop-decision.is-good')).toHaveTextContent('Fixedtest: tests/test_calc.py::test_divide');
    const roundOne = screen.getByText('Round 1').closest('.review-loop-round');
    const rejected = within(roundOne).getByText('zip extra parsing differs from Go').closest('li');
    expect(rejected.querySelector('.review-loop-decision.is-warn')).toHaveTextContent('RejectedGo reads extra.sub(fieldSize) first');
    const unanswered = screen.getByText('no limit on entries').closest('li');
    expect(unanswered.querySelector('.review-loop-decision.is-bad')).toHaveTextContent('Not answered');

    fireEvent.click(screen.getByText('The chat’s reply to the findings'));
    await screen.findByText(/Rejected R1-2: Go reads extra.sub first./);
    expect(kato.requests).toContain(`GET /api/sessions/UNA-1/review-loop/${LOOP_ID}/rounds/1/response`);
  });

  test('a new round appends below without moving or collapsing what is open', async () => {
    kato.loop = { ...summary(), rounds: [round(1), round(2)] };
    const { rerender, container } = render(
      <ReviewLoopPane session={session(summary())} onClose={() => {}} />,
    );
    await screen.findByText('bug in round 2');
    fireEvent.click(screen.getByText('Round 1'));            // the operator opens round 1
    const roundOne = container.querySelector('[data-round="1"]');

    kato.loop = { ...summary({ round: 3, phase: 'reviewing' }), rounds: [round(1), round(2), round(3)] };
    rerender(<ReviewLoopPane session={session(summary({ round: 3, phase: 'reviewing' }))} onClose={() => {}} />);
    await screen.findByText('bug in round 3');

    const order = [...container.querySelectorAll('.review-loop-round')].map((li) => li.dataset.round);
    expect(order).toEqual(['1', '2', '3']);
    expect(container.querySelector('[data-round="1"]')).toBe(roundOne);  // same node, not re-mounted
    expect(screen.getByText('bug in round 1')).toBeInTheDocument();       // still open
    expect(screen.getByText('bug in round 2')).toBeInTheDocument();       // not collapsed by round 3
  });

  test('a round\'s report is fetched only when opened', async () => {
    kato.loop = { ...summary(), rounds: [round(1)] };
    kato.artifacts['1/review'] = '**MAJOR** api/app.py:7 — bug';
    render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    await screen.findByText('bug in round 1');
    const artifactUrl = `GET /api/sessions/UNA-1/review-loop/${LOOP_ID}/rounds/1/review`;
    expect(kato.requests).not.toContain(artifactUrl);
    fireEvent.click(screen.getByText('Reviewer’s full report'));
    // The report's own text, inside the opened report — not the "MAJOR" chip
    // the findings list already shows, which let a report stuck on
    // "Loading…" pass this test.
    const report = await screen.findByText('api/app.py:7 — bug', { exact: false });
    expect(report.closest('.review-loop-artifact-body')).not.toBeNull();
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
    expect(kato.requests.filter((request) => request === artifactUrl)).toHaveLength(1);

    // Closed and opened again: shown from what was loaded, not fetched twice.
    fireEvent.click(screen.getByText('Reviewer’s full report'));
    fireEvent.click(screen.getByText('Reviewer’s full report'));
    expect(screen.getByText('api/app.py:7 — bug', { exact: false })).toBeInTheDocument();
    expect(kato.requests.filter((request) => request === artifactUrl)).toHaveLength(1);
  });

  test('Stop stops a running loop; a finished one offers Run again', async () => {
    kato.loop = { ...summary(), rounds: [round(1)] };
    const { rerender } = render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    await waitFor(() => expect(kato.requests).toContain('POST /api/sessions/UNA-1/review-loop/stop'));

    const finished = summary({ status: 'clean', phase: 'done', reason: 'round 2 found no blocking issues' });
    kato.loop = { ...finished, rounds: [round(1), round(2, { outcome: 'clean' })] };
    rerender(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    expect(screen.getByText(/finished clean after 2 rounds — round 2 found no blocking issues/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /run again/i }));
    await waitFor(() => expect(kato.requests).toContain('POST /api/sessions/UNA-1/review-loop'));
    expect(kato.startBodies).toEqual([{ ...DEFAULT_STAGES, max_rounds: 5 }]);
  });

  test('a loop cut off part-way offers Resume, which continues it — nothing is started', async () => {
    const failed = summary({
      status: 'failed', phase: 'done', round: 7, max_rounds: 10,
      reason: 'the review run failed: Claude CLI did not finish within 7200s',
      resume: 'run round 7’s review again',
    });
    kato.loop = { ...failed, rounds: [round(1)] };
    render(<ReviewLoopPane session={session(failed)} onClose={() => {}} />);
    const resume = screen.getByRole('button', { name: /resume/i });
    expect(resume).toHaveAttribute(
      'data-tooltip',
      'Continue this loop where it stopped: run round 7’s review again. '
        + 'It keeps its rounds, decisions, stages and model.',
    );
    // Run again is still there — a new loop from round 1 — and says so.
    expect(screen.getByRole('button', { name: /run again/i }))
      .toHaveAttribute('data-tooltip', 'Start a new loop from round 1 with the settings picked here.');

    let toasts = [];
    const unsubscribe = toastStore.subscribe((list) => { toasts = list; });
    fireEvent.click(resume);
    await waitFor(() => expect(kato.requests).toContain('POST /api/sessions/UNA-1/review-loop/resume'));
    expect(kato.startBodies).toEqual([]);
    await waitFor(() => expect(toasts.map((entry) => [entry.title, entry.message])).toContainEqual([
      'Review loop resumed', 'It picks up where it stopped: run round 7’s review again.',
    ]));
    unsubscribe();
  });

  test('a refused resume says why', async () => {
    const failed = summary({ status: 'failed', phase: 'done', resume: 'run round 2’s review again' });
    kato.loop = { ...failed, resume: '', rounds: [] };  // resumed elsewhere meanwhile
    render(<ReviewLoopPane session={session(failed)} onClose={() => {}} />);
    let toasts = [];
    const unsubscribe = toastStore.subscribe((list) => { toasts = list; });
    fireEvent.click(screen.getByRole('button', { name: /resume/i }));
    await waitFor(() => expect(toasts.map((entry) => [entry.title, entry.message])).toContainEqual([
      'Couldn’t resume the review loop', 'there is nothing to resume',
    ]));
    unsubscribe();
  });

  test('a loop with nothing left to resume, or still running, offers no Resume', () => {
    const clean = summary({ status: 'clean', phase: 'done', resume: '' });
    const { rerender } = render(<ReviewLoopPane session={session(clean)} onClose={() => {}} />);
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
    expect(screen.getByRole('button', { name: /run again/i })).not.toHaveAttribute('data-tooltip');
    rerender(<ReviewLoopPane session={session(summary({ resume: 'stale' }))} onClose={() => {}} />);
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
  });

  test('a task that never ran a loop explains it and offers Start', () => {
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    expect(screen.getByText(/independent reviewer reads the whole change/)).toBeInTheDocument();
    expect(screen.getByText(/Pick how many rounds above, then Start/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start/i })).toBeInTheDocument();
  });
});

describe('the stages — picked with the round limit, before a loop starts', () => {
  test('the self-check is off by default, the rest on; a tick goes into the start request', async () => {
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    const boxes = screen.getAllByRole('checkbox');
    expect(boxes.map((box) => box.closest('label').textContent)).toEqual([
      'Self-check in the main chat first',
      'Tests must pass before clean',
      'Clean-room check after the fixes',
      'One more sweep after any clean review',
    ]);
    expect(boxes.map((box) => box.checked)).toEqual([false, true, true, true]);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Self-check in the main chat first' }));
    expect(readReviewLoopStages().self_check).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /start/i }));
    await waitFor(() => expect(kato.startBodies).toEqual([
      { ...DEFAULT_STAGES, self_check: true, max_rounds: 5 },
    ]));
    expect(window.localStorage.getItem(STAGES_KEY)).toContain('"self_check":true');
  });

  test('a running loop shows no stage boxes — they were fixed when it started', () => {
    kato.loop = { ...summary(), rounds: [] };
    render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  test('a stage missing from storage (added later) takes its own default', () => {
    window.localStorage.setItem(STAGES_KEY, '{"verify_tests":false,"extra_sweep":"yes"}');
    _resetReviewLoopStages();
    expect(readReviewLoopStages()).toEqual({ ...DEFAULT_STAGES, verify_tests: false });
  });

  test('a v1 record (it stored the self-check as on for everyone) is not read', () => {
    window.localStorage.setItem('kato.reviewLoopStages.v1', '{"self_check":true}');
    _resetReviewLoopStages();
    expect(readReviewLoopStages().self_check).toBe(false);
  });
});

describe('the view shows the stages that ran', () => {
  test('a clean-room sweep is tagged, and a round shows its test result', async () => {
    const tested = round(1, {
      outcome: 'tests_failed', findings: [], counts: { BLOCKER: 0, MAJOR: 0, MINOR: 0, NIT: 0 },
      tests: { passed: false, command: 'pytest', summary: '1 failed', failures: ['test_divide: ZeroDivisionError'] },
    });
    const sweep = round(2, {
      outcome: 'clean', sweep: true, blind: true, sent_at: 0, findings: [],
      counts: { BLOCKER: 0, MAJOR: 0, MINOR: 0, NIT: 0 },
    });
    const finished = summary({ status: 'clean', phase: 'done' });
    kato.loop = { ...finished, rounds: [tested, sweep] };
    render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    const sweepRow = (await screen.findByText('Round 2')).closest('.review-loop-round');
    expect(within(sweepRow).getByText('Clean-room')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Round 1'));
    const roundOne = screen.getByText('Round 1').closest('.review-loop-round');
    expect(within(roundOne).getByText('Tests failed — 1 failing')).toBeInTheDocument();
    expect(within(roundOne).getByText('test_divide: ZeroDivisionError')).toBeInTheDocument();
    expect(roundOne.querySelector('.review-loop-tests.is-bad')).not.toBeNull();
    expect(within(roundOne).getByText('tests failed — sent back')).toBeInTheDocument();
    // Failing tests sent back carry no per-finding reply, but do have a test report.
    expect(within(roundOne).queryByText('The chat’s reply to the findings')).not.toBeInTheDocument();
    expect(within(roundOne).getByText('The chat’s test report')).toBeInTheDocument();
  });

  test('the main chat\'s self-check turns are listed above the rounds', async () => {
    const running = summary({ phase: 'self_check', round: 0, self_check_turn: 2 });
    kato.loop = {
      ...running, rounds: [],
      self_checks: [
        { number: 1, started_at: NOW - 100, finished_at: NOW - 60, clean: false, fixed: 2, summary: 'fixed the empty-list case' },
        { number: 2, started_at: NOW - 50, finished_at: 0, clean: null, fixed: 0, summary: '' },
      ],
    };
    const { container } = render(<ReviewLoopPane session={session(running)} onClose={() => {}} />);
    // Each turn is a collapsed row, like a round: what it fixed, how long, outcome.
    const first = (await screen.findByText('Self-check 1')).closest('.review-loop-round');
    expect(first).toHaveClass('is-self-check', 'is-unclean');
    expect(within(first).getByText('fixed 2')).toBeInTheDocument();
    expect(within(first).getByText('40s')).toBeInTheDocument();
    expect(within(first).getByText('not clean yet')).toBeInTheDocument();
    expect(within(first).getByRole('button')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('fixed the empty-list case')).not.toBeInTheDocument();
    const second = screen.getByText('Self-check 2').closest('.review-loop-round');
    expect(within(second).getByText('checking…')).toBeInTheDocument();
    expect(within(second).getByText('in progress')).toBeInTheDocument();
    // Opened, it shows what the chat said, and — finished turns only — its reply.
    fireEvent.click(screen.getByText('Self-check 1'));
    expect(within(first).getByText('fixed the empty-list case')).toBeInTheDocument();
    expect(within(first).getByText('The chat’s self-check reply')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Self-check 2'));
    expect(screen.getAllByText('The chat’s self-check reply')).toHaveLength(1);
    fireEvent.click(screen.getByText('Self-check 1'));
    expect(screen.queryByText('fixed the empty-list case')).not.toBeInTheDocument();
    expect(container.querySelectorAll('[data-self-check]')).toHaveLength(2);
    // The tracker says where it is.
    expect(screen.getByText('Self-check in the main chat (turn 2)')).toBeInTheDocument();
  });
});

describe('the round limit — picked before a loop starts', () => {
  const picker = () => screen.getByRole('combobox', { name: 'Review loop rounds' });

  test('it offers 1 to 30 rounds and starts at 5', () => {
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    const options = within(picker()).getAllByRole('option').map((option) => option.textContent);
    const many = Array.from({ length: 29 }, (_unused, index) => `${index + 2} rounds`);
    expect(options).toEqual(['1 round', ...many]);
    expect(picker()).toHaveValue('5');
  });

  test('the picked number is what runs, and the next loop remembers it', async () => {
    const { unmount } = render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    fireEvent.change(picker(), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /start/i }));
    await waitFor(() => expect(kato.startBodies).toEqual([{ ...DEFAULT_STAGES, max_rounds: 3 }]));
    expect(window.localStorage.getItem(ROUNDS_KEY)).toBe('{"rounds":3}');
    unmount();

    // A fresh page load reads it back from storage.
    _resetReviewLoopRounds();
    const finished = summary({ status: 'clean', phase: 'done', max_rounds: 3 });
    kato.loop = { ...finished, rounds: [] };
    render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    expect(picker()).toHaveValue('3');
    fireEvent.click(screen.getByRole('button', { name: /run again/i }));
    await waitFor(() => expect(kato.startBodies).toEqual([
      { ...DEFAULT_STAGES, max_rounds: 3 }, { ...DEFAULT_STAGES, max_rounds: 3 },
    ]));
  });

  test('a running loop has no picker — its limit was set when it started', () => {
    kato.loop = { ...summary(), rounds: [] };
    render(<ReviewLoopPane session={session(summary())} onClose={() => {}} />);
    expect(screen.queryByRole('combobox', { name: 'Review loop rounds' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument();
  });

  test('a stored value out of range or not a number never reaches the server', () => {
    for (const [stored, expected] of [['{"rounds":0}', 1], ['{"rounds":99}', 30],
      ['{"rounds":2.5}', 5], ['{"rounds":"7"}', 7], ['not json', 5], ['{"rounds":null}', 5]]) {
      window.localStorage.setItem(ROUNDS_KEY, stored);
      _resetReviewLoopRounds();
      expect(readReviewLoopRounds()).toBe(expected);
    }
  });
});

describe('ReviewLoopButton / chip / tab badge — the indicators', () => {
  test('no loop yet: the button opens the view and starts nothing', () => {
    // The rounds are picked in the view first; nothing runs from the header.
    const requests = openRequests();
    render(<ReviewLoopButton session={session(null)} />);
    const button = screen.getByRole('button', { name: 'Review loop' });
    expect(button).toHaveAttribute('data-tooltip', expect.stringMatching(/pick how many rounds and start/));
    fireEvent.click(button);
    expect(requests.seen().map((r) => r.taskId)).toEqual(['UNA-1']);
    expect(kato.requests).toEqual([]);
    requests.unsubscribe();
  });

  test('every mark of the loop is the loop glyph', () => {
    const { container } = render(
      <>
        <ReviewLoopButton session={session(summary())} />
        <ReviewLoopChip loop={summary()} taskId="UNA-1" />
        <ReviewLoopTabBadge loop={summary()} />
      </>,
    );
    for (const selector of ['.review-loop-button', '.review-loop-chip', '.tab-review-loop-badge']) {
      expect(container.querySelector(`${selector} [data-icon="loop"]`)).not.toBeNull();
    }
  });

  test('a loop exists: the button opens the view and starts nothing', () => {
    const requests = openRequests();
    const { container } = render(<ReviewLoopButton session={session(summary())} />);
    expect(container.querySelector('.review-loop-dot.is-running')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review loop' }));
    expect(requests.seen().map((r) => r.taskId)).toEqual(['UNA-1']);
    expect(kato.requests).toEqual([]);
    requests.unsubscribe();
  });

  test('the placeholder button is inert', () => {
    render(<ReviewLoopButton disabled />);
    expect(screen.getByRole('button', { name: 'Review loop' })).toBeDisabled();
  });

  test('the chip shows where a running loop is', () => {
    render(<ReviewLoopChip loop={summary()} taskId="UNA-1" />);
    expect(screen.getByText(/^2\/5 · fixing · \d+s$/)).toBeInTheDocument();
  });

  test('a finished loop\'s outcome shows until it has been seen', () => {
    const finished = summary({ status: 'stuck', phase: 'done' });
    const { rerender, container } = render(<ReviewLoopChip loop={finished} taskId="UNA-1" />);
    expect(screen.getByText('stuck')).toBeInTheDocument();
    markReviewLoopSeen(LOOP_ID);
    rerender(<ReviewLoopChip loop={{ ...finished }} taskId="UNA-1" />);
    expect(container.querySelector('.review-loop-chip')).not.toBeInTheDocument();
  });

  test('the tab badge appears for a running loop and not without one', () => {
    const { container, rerender } = render(<ReviewLoopTabBadge loop={summary()} />);
    expect(container.querySelector('.tab-review-loop-badge.is-running')).toBeInTheDocument();
    rerender(<ReviewLoopTabBadge loop={null} />);
    expect(container.querySelector('.tab-review-loop-badge')).not.toBeInTheDocument();
  });
});

describe('the transcript — kato\'s message is labelled as kato\'s', () => {
  function renderChat(text) {
    return render(
      <EventLog entries={[{ source: 'history', raw: { type: 'user', uuid: 'u1', message: { content: text } } }]} />,
    );
  }

  test('the findings message is not "You asked", and opens the loop', () => {
    const requests = openRequests();
    renderChat('WORKSPACE SCOPE …\n\nKato review loop — round 2 of 5\n\n1. [MAJOR] api/app.py:7 — bug');
    expect(screen.getByText('Kato · review loop round 2/5')).toBeInTheDocument();
    expect(screen.queryByText('You asked')).not.toBeInTheDocument();
    const open = screen.getByRole('button', { name: 'Open the review loop' });
    expect(open.querySelector('[data-icon="loop"]')).not.toBeNull();
    act(() => { fireEvent.click(open); });
    expect(requests.seen().map((r) => r.taskId)).toEqual(['']);
    requests.unsubscribe();
  });

  test('an operator\'s own prompt is still "You asked"', () => {
    renderChat('please fix the login');
    expect(screen.getByText('You asked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open the review loop' })).not.toBeInTheDocument();
  });
});

describe('open review-loop tabs survive a reload', () => {
  test('the set and the active tab round-trip through storage', () => {
    window.localStorage.removeItem('kato.reviewLoopTabs.v1');
    expect(readReviewLoopTabs()).toEqual({ tabs: [], active: '' });

    writeReviewLoopTabs(['UNA-1', 'UNA-2', 'UNA-1'], 'UNA-2');
    expect(readReviewLoopTabs()).toEqual({ tabs: ['UNA-1', 'UNA-2'], active: 'UNA-2' });

    // An active id no longer in the set falls back to the first tab.
    writeReviewLoopTabs(['UNA-3'], 'GONE');
    expect(readReviewLoopTabs()).toEqual({ tabs: ['UNA-3'], active: 'UNA-3' });

    window.localStorage.setItem('kato.reviewLoopTabs.v1', 'not json');
    expect(readReviewLoopTabs()).toEqual({ tabs: [], active: '' });
  });
});

describe('the reviewer\'s model — picked before a loop starts', () => {
  const picker = () => screen.getByRole('combobox', { name: 'Review loop model' });
  const optionNames = () => within(picker()).getAllByRole('option').map((option) => option.textContent);

  test('it names kato\'s default model and starts on it without sending one', async () => {
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    await waitFor(() => expect(optionNames()).toEqual([
      'Opus 5.5 (1M context) — kato\'s default', 'Opus 5.5', 'Sonnet 5.5',
    ]));
    expect(picker()).toHaveValue('claude-opus-5-5[1m]');
    fireEvent.click(screen.getByRole('button', { name: /start/i }));
    await waitFor(() => expect(kato.startBodies).toEqual([{ ...DEFAULT_STAGES, max_rounds: 5 }]));
  });

  test('a picked model is what runs, and the next loop remembers it', async () => {
    const { unmount } = render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    await waitFor(() => expect(optionNames()).toHaveLength(3));
    fireEvent.change(picker(), { target: { value: 'sonnet' } });
    expect(readReviewLoopModel()).toBe('sonnet');
    fireEvent.click(screen.getByRole('button', { name: /start/i }));
    await waitFor(() => expect(kato.startBodies).toEqual([
      { ...DEFAULT_STAGES, max_rounds: 5, model: 'sonnet' },
    ]));
    unmount();
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    await waitFor(() => expect(picker()).toHaveValue('sonnet'));
  });

  test('picking the default entry stores no pick, so it follows kato\'s setting', async () => {
    writeReviewLoopModel('sonnet');
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    await waitFor(() => expect(optionNames()).toHaveLength(3));
    fireEvent.change(picker(), { target: { value: 'claude-opus-5-5[1m]' } });
    expect(readReviewLoopModel()).toBe('');
  });

  test('a remembered model no longer offered is dropped, never sent unseen', async () => {
    writeReviewLoopModel('retired-model');
    render(<ReviewLoopPane session={session(null)} onClose={() => {}} />);
    await waitFor(() => expect(readReviewLoopModel()).toBe(''));
    expect(picker()).toHaveValue('claude-opus-5-5[1m]');
  });

  test('a running loop names the model reviewing it', async () => {
    const running = summary({ model: 'sonnet' });
    kato.loop = { ...running, rounds: [] };
    const { rerender } = render(<ReviewLoopPane session={session(running)} onClose={() => {}} />);
    expect(await screen.findByText('Reviewed by Sonnet 5.5')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Review loop model' })).not.toBeInTheDocument();
    const onDefault = summary({ model: '' });
    rerender(<ReviewLoopPane session={session(onDefault)} onClose={() => {}} />);
    expect(await screen.findByText('Reviewed by Opus 5.5 (1M context)')).toBeInTheDocument();
  });
});

describe('open and closed rounds are remembered — for the latest loop only', () => {
  const header = (container, number) => container.querySelector(`[data-round="${number}"] .review-loop-round-header`);
  const turnHeader = (container, number) => container.querySelector(`[data-self-check="${number}"] .review-loop-round-header`);

  test('what the operator opened and closed survives closing the view', async () => {
    const finished = summary({ status: 'clean', phase: 'done' });
    kato.loop = { ...finished, rounds: [round(1), round(2, { outcome: 'clean' })] };
    const first = render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    await screen.findByText('Round 2');
    // The newest opened itself; the operator closes it and opens round 1.
    await waitFor(() => expect(header(first.container, 2)).toHaveAttribute('aria-expanded', 'true'));
    fireEvent.click(header(first.container, 2));
    fireEvent.click(header(first.container, 1));
    first.unmount();

    const again = render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    await screen.findByText('Round 2');
    expect(header(again.container, 2)).toHaveAttribute('aria-expanded', 'false');
    expect(header(again.container, 1)).toHaveAttribute('aria-expanded', 'true');
  });

  test('an opened self-check turn is remembered too', async () => {
    const finished = summary({ status: 'clean', phase: 'done' });
    kato.loop = {
      ...finished, rounds: [],
      self_checks: [{ number: 1, started_at: NOW - 60, finished_at: NOW - 30, clean: true, fixed: 0, summary: 'all good' }],
    };
    const first = render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    await screen.findByText('Self-check 1');
    fireEvent.click(turnHeader(first.container, 1));
    first.unmount();
    const again = render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    await screen.findByText('Self-check 1');
    expect(turnHeader(again.container, 1)).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('all good')).toBeInTheDocument();
  });

  test('another loop never inherits them, and Run again forgets the last loop', async () => {
    const finished = summary({ status: 'clean', phase: 'done' });
    kato.loop = { ...finished, rounds: [round(1), round(2, { outcome: 'clean' })] };
    const first = render(<ReviewLoopPane session={session(finished)} onClose={() => {}} />);
    await screen.findByText('Round 2');
    fireEvent.click(header(first.container, 1));  // open round 1 of this loop
    expect(window.localStorage.getItem('kato.reviewLoopExpanded.v1')).toContain(LOOP_ID);

    fireEvent.click(screen.getByRole('button', { name: /run again/i }));
    await waitFor(() => expect(kato.requests).toContain('POST /api/sessions/UNA-1/review-loop'));
    await waitFor(() => expect(window.localStorage.getItem('kato.reviewLoopExpanded.v1')).not.toContain(LOOP_ID));
    first.unmount();

    // The next loop has rounds with the same numbers: round 1 starts closed.
    const next = { ...summary({ status: 'clean', phase: 'done' }), loop_id: 'c'.repeat(32) };
    kato.loop = { ...next, rounds: [round(1), round(2, { outcome: 'clean' })] };
    const again = render(<ReviewLoopPane session={session(next)} onClose={() => {}} />);
    await screen.findByText('Round 2');
    expect(header(again.container, 1)).toHaveAttribute('aria-expanded', 'false');
  });
});

