// Tests for UsageDialog — what each task has consumed.
//
// The dialog keeps three groups apart that a lazy sum would blur together: what
// the model WROTE, what was sent to it NEW, and the context it RE-READ on every
// turn. A row leads with its TOKEN total and one plain bar (tokens against the
// biggest task); what a task is worth in dollars — priced on the server, per
// message, per model — is in its details. The bar must never depend on a price:
// a server that does not send one still gets a full set of bars.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchUsage: vi.fn(),
}));

import { fetchUsage } from '../api.js';
import UsageDialog from './UsageDialog.jsx';
import { formatSpan } from '../utils/usage.js';
import { _resetUsageShowDeleted } from '../utils/usageDeletedPref.js';

function usage(overrides = {}) {
  return {
    input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0, turns: 0,
    first_at: '', last_at: '', models: {},
    cost: { output: 0, fresh: 0, cache_read: 0, total: 0 }, unpriced_models: [],
    ...overrides,
  };
}

function task(id, summary, usageOverrides, extra = {}) {
  return {
    task_id: id, summary, status: 'active', chats: 1, uncounted_backends: [],
    usage: usage(usageOverrides), ...extra,
  };
}

// Opus 5 at $5 in / $25 out / $0.50 read: 7M out = $175, 70M written (x1.25)
// + 40k in = $437.70, 1.4B read = $700.
const BIG_COST = { output: 175, fresh: 437.7, cache_read: 700, total: 1312.7 };
const BIG = task('UNA-1', 'Big refactor', {
  input: 40_000, output: 7_000_000, cache_read: 1_400_000_000, cache_write: 70_000_000,
  total: 1_477_040_000, turns: 3015, last_at: '2026-10-16T03:00:00Z',
  cost: BIG_COST,
  models: {
    'claude-opus-5': {
      input: 40_000, output: 7_000_000, cache_read: 1_400_000_000, cache_write: 70_000_000,
      total: 1_477_040_000, turns: 3015, label: 'Opus 5', priced: true, cost: BIG_COST,
      first_at: '2026-10-10T00:00:00Z', last_at: '2026-10-16T03:00:00Z',
    },
  },
});
const SMALL_COST = { output: 0.0225, fresh: 0.0005, cache_read: 0.0045, total: 0.0275 };
const SMALL = task('UNA-2', 'Tiny fix', {
  input: 100, output: 900, cache_read: 9_000, cache_write: 0, total: 10_000, turns: 4,
  cost: SMALL_COST,
});

function report(tasks, extra = {}) {
  const totals = tasks.reduce((sum, t) => usage({
    input: sum.input + t.usage.input,
    output: sum.output + t.usage.output,
    cache_read: sum.cache_read + t.usage.cache_read,
    cache_write: sum.cache_write + t.usage.cache_write,
    total: sum.total + t.usage.total,
    turns: sum.turns + t.usage.turns,
    cost: {
      output: sum.cost.output + (t.usage.cost?.output || 0),
      fresh: sum.cost.fresh + (t.usage.cost?.fresh || 0),
      cache_read: sum.cost.cache_read + (t.usage.cost?.cache_read || 0),
      total: sum.cost.total + (t.usage.cost?.total || 0),
    },
    unpriced_models: [...sum.unpriced_models, ...(t.usage.unpriced_models || [])],
  }), usage());
  // A server that does not price sends no cost at all — not even on the totals.
  if (tasks.some((t) => !t.usage.cost)) {
    delete totals.cost;
    delete totals.unpriced_models;
  }
  return { tasks, totals, uncounted_backends: [], pricing_as_of: '2026-10-06', ...extra };
}

async function open(tasks, extra) {
  fetchUsage.mockResolvedValue(report(tasks, extra));
  const view = render(<UsageDialog onClose={() => {}} />);
  await screen.findByText(tasks[0].summary);
  return view;
}

function rowOrder() {
  return screen.getAllByRole('listitem').map((row) => within(row).getByText(/UNA-\d/).textContent);
}

beforeEach(() => {
  fetchUsage.mockReset();
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetUsageShowDeleted();
});

describe('UsageDialog', () => {
  test('lists every task with its token total next to the bar, biggest first', async () => {
    await open([SMALL, BIG]);
    expect(rowOrder()).toEqual(['UNA-1', 'UNA-2']);
    expect(screen.getByText('Big refactor')).toBeInTheDocument();
    const [big, small] = screen.getAllByRole('listitem');
    // 1.477B reads as billions, not "1477M"; the exact count is on hover.
    expect(within(big).getByText('1.5B')).toHaveAttribute('title', '1,477,040,000 tokens');
    expect(within(small).getByText('10k')).toBeInTheDocument();
  });

  test('a row carries no money: that is in the details', async () => {
    await open([BIG]);
    expect(screen.getByRole('listitem').textContent).not.toContain('$');
  });

  test('sorts by tokens by default, and by cost when asked', async () => {
    // Fewer tokens but dearer: it wrote more, and writing is the dear part.
    const wordy = task('UNA-3', 'Wrote a lot', {
      output: 2_000_000, cache_read: 1_000, total: 2_001_000, turns: 9,
      cost: { output: 50, fresh: 0, cache_read: 0, total: 50 },
    });
    const cheapReader = task('UNA-4', 'Mostly re-read', {
      output: 100, cache_read: 900_000_000, total: 900_000_100, turns: 800,
      cost: { output: 0, fresh: 0, cache_read: 450, total: 450 },
    });
    await open([wordy, cheapReader]);
    expect(screen.getByLabelText('Sort by')).toHaveValue('total');
    expect(rowOrder()).toEqual(['UNA-4', 'UNA-3']);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'cost' } });
    expect(rowOrder()).toEqual(['UNA-4', 'UNA-3']);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'output' } });
    expect(rowOrder()).toEqual(['UNA-3', 'UNA-4']);
  });

  test('"Highest cost" really orders by dollars, not tokens', async () => {
    const wordy = task('UNA-3', 'Wrote a lot', {
      output: 2_000_000, total: 2_001_000, turns: 9,
      cost: { output: 500, fresh: 0, cache_read: 0, total: 500 },
    });
    const cheapReader = task('UNA-4', 'Mostly re-read', {
      cache_read: 900_000_000, total: 900_000_100, turns: 800,
      cost: { output: 0, fresh: 0, cache_read: 450, total: 450 },
    });
    await open([wordy, cheapReader]);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'cost' } });
    expect(rowOrder()).toEqual(['UNA-3', 'UNA-4']);
  });

  test('keeps output, new input and cache reads apart in the summary tiles', async () => {
    const { container } = await open([BIG]);
    const tiles = container.querySelectorAll('.usage-tile');
    expect(tiles).toHaveLength(4);
    expect(tiles[0].textContent).toContain('Total tokens');
    expect(tiles[0].textContent).toContain('1.5B');
    // The one place the summary mentions money: small, in the first tile's hint.
    expect(tiles[0].textContent).toContain('≈ $1,313 at API rates');
    expect(tiles[1].textContent).toContain('Written by the model');
    expect(tiles[1].textContent).toContain('7.0M');
    // "New input" is input + cache writes — 40k + 70M — and not the cache reads.
    expect(tiles[2].textContent).toContain('70M');
    expect(tiles[3].textContent).toContain('1.4B');
    expect(tiles[1].textContent + tiles[2].textContent + tiles[3].textContent).not.toContain('$');
  });

  test('a row says how the task splits, not just how big it is', async () => {
    await open([BIG]);
    const row = screen.getByRole('listitem');
    expect(row.textContent).toContain('7.0M output');
    expect(row.textContent).toContain('70M new input');
    expect(row.textContent).toContain('1.4B cache read');
    expect(row.textContent).toContain('3,015 turns');
  });

  test('sorting by output reorders the list', async () => {
    const quiet = task('UNA-3', 'Chatty but small', { output: 5_000_000, total: 5_000_000 });
    await open([BIG, SMALL, quiet]);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'turns' } });
    expect(rowOrder()).toEqual(['UNA-1', 'UNA-2', 'UNA-3']);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'output' } });
    expect(rowOrder()).toEqual(['UNA-1', 'UNA-3', 'UNA-2']);
  });

  test('a row opens to show the models behind it', async () => {
    await open([BIG]);
    const head = screen.getByRole('button', { name: /UNA-1/ });
    expect(head).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('table')).toBeNull();

    fireEvent.click(head);

    expect(head).toHaveAttribute('aria-expanded', 'true');
    const table = screen.getByRole('table');
    // The person-readable name, with the model's own id on hover.
    expect(within(table).getByRole('rowheader', { name: 'Opus 5' })).toHaveAttribute('title', 'claude-opus-5');
    expect(within(table).getByText('$1,313')).toBeInTheDocument();
    // …and the task's cost, itemised, above it.
    const cost = document.querySelector('.usage-cost');
    expect(cost).toHaveTextContent('Estimated cost $1,313 at API rates');
    expect(cost).toHaveTextContent('$175 written by the model · $438 new input · $700 re-read from cache');
    expect(screen.getByText('1 chat counted.')).toBeInTheDocument();

    fireEvent.click(head);
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('names the agents whose chats could not be counted', async () => {
    await open([BIG], { uncounted_backends: ['codex'] });
    expect(screen.getByText(/Codex chats are not counted/)).toBeInTheDocument();
  });

  test('an empty report says so instead of showing a blank list', async () => {
    fetchUsage.mockResolvedValue(report([]));
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByText('No task has used any tokens yet.')).toBeInTheDocument();
  });

  test('a failed read is reported, and Refresh tries again', async () => {
    fetchUsage.mockRejectedValueOnce(new Error('503 unavailable'));
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read usage: 503 unavailable');

    fetchUsage.mockResolvedValue(report([BIG]));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Big refactor')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  test('Refresh keeps the list on screen while it re-reads', async () => {
    await open([BIG]);
    let finish;
    fetchUsage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    expect(screen.getByRole('button', { name: 'Reading…' })).toBeDisabled();
    expect(screen.getByText('Big refactor')).toBeInTheDocument();
    finish(report([BIG, SMALL]));
    await waitFor(() => expect(screen.getByText('Tiny fix')).toBeInTheDocument());
  });

  test('closes from the × button and from Escape', async () => {
    fetchUsage.mockResolvedValue(report([BIG]));
    const onClose = vi.fn();
    render(<UsageDialog onClose={onClose} />);
    await screen.findByText('Big refactor');

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  // ----- the bar -----

  test('says what a bar is, in one line', async () => {
    await open([BIG]);
    expect(document.querySelector('.usage-caption')).toHaveTextContent(
      'Each bar compares a task\'s tokens with the biggest task\'s — the longest bar.',
    );
  });

  test('the biggest task fills its bar and a smaller one is as long as its share of that', async () => {
    const half = task('UNA-5', 'Half as big', { total: 738_520_000, turns: 2 });
    const { container } = await open([BIG, half]);
    const fills = [...container.querySelectorAll('.usage-bar-fill')].map((f) => parseFloat(f.style.width));
    expect(fills[0]).toBeCloseTo(100, 5);
    expect(fills[1]).toBeCloseTo(50, 5);
  });

  test('a bar is one plain fill, not colour segments to decode', async () => {
    const { container } = await open([BIG]);
    expect(container.querySelectorAll('.usage-bar .usage-bar-fill')).toHaveLength(1);
    expect(container.querySelector('.usage-bar-seg')).toBeNull();
    expect(container.querySelector('.usage-legend')).toBeNull();
  });

  test('a tiny task still gets a visible sliver, never an empty bar', async () => {
    const { container } = await open([BIG, SMALL]);
    const small = container.querySelectorAll('.usage-bar-fill')[1];
    expect(small.style.minWidth).toBe('2px');
  });

  test('hovering the bar gives the exact count and both shares', async () => {
    const { container } = await open([BIG, SMALL]);
    const title = container.querySelectorAll('.usage-bar')[0].getAttribute('title');
    expect(title).toBe('1,477,040,000 tokens — 100% of everything counted, 100% of the biggest task');
    const small = container.querySelectorAll('.usage-bar')[1].getAttribute('title');
    expect(small).toBe('10,000 tokens — <1% of everything counted, 0% of the biggest task');
  });

  test('the bars are drawn even when the server sends no prices (an older kato)', async () => {
    const old = (t) => {
      const copy = { ...t, usage: { ...t.usage } };
      delete copy.usage.cost;
      delete copy.usage.unpriced_models;
      return copy;
    };
    const { container } = await open([old(BIG), old(SMALL)], { pricing_as_of: undefined });
    const fills = [...container.querySelectorAll('.usage-bar-fill')].map((f) => parseFloat(f.style.width));
    expect(fills[0]).toBeCloseTo(100, 5);
    expect(fills[1]).toBeGreaterThan(0);
    // …and no money anywhere, not a false $0.
    expect(document.body.textContent).not.toContain('$');
    fireEvent.click(screen.getByRole('button', { name: /UNA-1/ }));
    expect(screen.queryByRole('columnheader', { name: 'Cost' })).toBeNull();
    expect(document.querySelector('.usage-cost')).toBeNull();
  });

  // ----- which models, and when -----

  const SWITCHED = task('UNA-7', 'Switched mid-way', {
    output: 100, cache_read: 1_000, total: 1_100, turns: 999, last_at: '2026-10-16T03:00:00Z',
    cost: { output: 3, fresh: 0, cache_read: 5, total: 8 },
    models: {
      // Listed out of order on purpose: the dialog orders them by first use.
      'claude-sonnet-5-5': {
        label: 'Sonnet 5.5', priced: true, turns: 579, output: 70, input: 0, cache_write: 0,
        cache_read: 800, total: 870, cost: { output: 1, fresh: 0, cache_read: 2, total: 3 },
        first_at: '2026-10-12T09:00:00Z', last_at: '2026-10-16T03:00:00Z',
      },
      'claude-opus-5-5': {
        label: 'Opus 5.5', priced: true, turns: 420, output: 30, input: 0, cache_write: 0,
        cache_read: 200, total: 230, cost: { output: 2, fresh: 0, cache_read: 3, total: 5 },
        first_at: '2026-10-10T09:00:00Z', last_at: '2026-10-12T08:00:00Z',
      },
    },
  });

  test('a row names the models in the order the task used them', async () => {
    const { container } = await open([SWITCHED]);
    const chips = [...container.querySelectorAll('.usage-model-chip')].map((c) => c.textContent);
    expect(chips).toEqual(['Opus 5.5', 'Sonnet 5.5']);
    expect(container.querySelector('.usage-model-arrow')).toBeInTheDocument();
    // The raw id and the turns are on hover, not on the row.
    expect(screen.getByText('Opus 5.5')).toHaveAttribute('title', 'claude-opus-5-5 · 420 turns');
  });

  test('the table shows each model, when it ran, how much it did and what that cost', async () => {
    await open([SWITCHED]);
    fireEvent.click(screen.getByRole('button', { name: /UNA-7/ }));
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole('rowheader')).toHaveTextContent('Opus 5.5');
    expect(rows[0]).toHaveTextContent('420');
    expect(rows[0]).toHaveTextContent('$5.00');
    expect(within(rows[1]).getByRole('rowheader')).toHaveTextContent('Sonnet 5.5');
    expect(rows[1]).toHaveTextContent('579');
    expect(rows[1]).toHaveTextContent('$3.00');
    expect(document.querySelector('.usage-cost')).toHaveTextContent('Estimated cost $8.00');
    // "Used" is a span, in the same relative terms the rest of the dialog uses.
    expect(rows[0].cells[1].textContent).toMatch(/ago/);
  });

  test('a model with no price is marked in the details, costs "—", and makes the cost a minimum', async () => {
    const odd = task('UNA-8', 'Unknown model', {
      output: 500, total: 500, turns: 3, last_at: '2026-10-16T03:00:00Z',
      cost: { output: 4, fresh: 0, cache_read: 0, total: 4 },
      unpriced_models: ['claude-made-up-9'],
      models: {
        'claude-opus-5': {
          label: 'Opus 5', priced: true, turns: 2, output: 100, total: 100, input: 0,
          cache_write: 0, cache_read: 0, cost: { output: 4, fresh: 0, cache_read: 0, total: 4 },
        },
        'claude-made-up-9': {
          label: 'claude-made-up-9', priced: false, turns: 1, output: 400, total: 400, input: 0,
          cache_write: 0, cache_read: 0, cost: { output: 0, fresh: 0, cache_read: 0, total: 0 },
        },
      },
    });
    await open([odd]);
    // The row stays about tokens; the money is not on it, not even a floor.
    expect(screen.getByRole('listitem').textContent).not.toContain('$');
    fireEvent.click(screen.getByRole('button', { name: /UNA-8/ }));
    // A minimum, never a confident figure.
    expect(document.querySelector('.usage-cost')).toHaveTextContent('Estimated cost $4.00+');
    const unknown = within(screen.getByRole('table')).getByRole('rowheader', { name: /claude-made-up-9/ });
    expect(unknown).toHaveTextContent('no price');
    expect(unknown.closest('tr')).toHaveTextContent('—');
    expect(screen.getByText(/its tokens are counted but its cost is not/)).toBeInTheDocument();
  });

  test('a task none of whose models could be priced shows a dash for its cost, not $0', async () => {
    const odd = task('UNA-9', 'Nothing priced', {
      output: 500, total: 500, turns: 1, unpriced_models: ['claude-made-up-9'],
      models: { 'claude-made-up-9': { label: 'x', priced: false, turns: 1, total: 500 } },
    });
    await open([odd]);
    fireEvent.click(screen.getByRole('button', { name: /UNA-9/ }));
    expect(document.querySelector('.usage-cost')).toHaveTextContent('Estimated cost —');
  });

  test('the footnote says costs are estimates, as of the price date, not a bill', async () => {
    await open([BIG]);
    const foot = document.querySelector('.usage-foot');
    expect(foot).toHaveTextContent("estimates at Anthropic's published API rates (as of 2026-10-06)");
    expect(foot).toHaveTextContent('not as what you paid');
    expect(foot).toHaveTextContent("in each task's details");
  });

  // ----- the "Used" column against an older server (no per-model times) -----

  function olderServerTask(id, models, extra = {}) {
    const usageFields = {
      output: 100, total: 1000, turns: 5,
      first_at: '2026-10-10T00:00:00Z', last_at: '2026-10-12T00:00:00Z', models, ...extra,
    };
    const old = task(id, `Task ${id}`, usageFields);
    delete old.usage.cost;
    delete old.usage.unpriced_models;
    return old;
  }

  test('a task on one model shows that model\'s span even from a server that sends none per model', async () => {
    const old = olderServerTask('UNA-10', {
      'claude-opus-5-5': { turns: 5, output: 100, input: 0, cache_write: 0, cache_read: 900, total: 1000 },
    });
    await open([old]);
    fireEvent.click(screen.getByRole('button', { name: /UNA-10/ }));
    const table = screen.getByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Used' })).toBeInTheDocument();
    const [row] = within(table).getAllByRole('row').slice(1);
    const used = row.cells[1].textContent;
    expect(used).toBe(formatSpan('2026-10-10T00:00:00Z', '2026-10-12T00:00:00Z'));
    expect(used).not.toBe('');
  });

  test('with several models and no times at all, the Used column is left out, not blank', async () => {
    const old = olderServerTask('UNA-11', {
      'claude-opus-5-5': { turns: 3, output: 60, input: 0, cache_write: 0, cache_read: 0, total: 600 },
      'claude-sonnet-5-5': { turns: 2, output: 40, input: 0, cache_write: 0, cache_read: 0, total: 400 },
    });
    await open([old]);
    fireEvent.click(screen.getByRole('button', { name: /UNA-11/ }));
    const table = screen.getByRole('table');
    expect(within(table).queryByRole('columnheader', { name: 'Used' })).toBeNull();
    // Every row still has one cell per remaining column (no stray empty cell).
    const header = within(table).getAllByRole('row')[0].cells.length;
    within(table).getAllByRole('row').slice(1).forEach((row) => {
      expect(row.cells.length).toBe(header);
    });
  });

  test('a model with no time, beside one that has it, shows a dash rather than an empty cell', async () => {
    const mixed = olderServerTask('UNA-12', {
      'claude-opus-5-5': {
        turns: 3, output: 60, input: 0, cache_write: 0, cache_read: 0, total: 600,
        first_at: '2026-10-10T00:00:00Z', last_at: '2026-10-11T00:00:00Z',
      },
      'claude-sonnet-5-5': { turns: 2, output: 40, input: 0, cache_write: 0, cache_read: 0, total: 400 },
    });
    await open([mixed]);
    fireEvent.click(screen.getByRole('button', { name: /UNA-12/ }));
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows[0].cells[1].textContent).not.toBe('—');
    expect(rows[1].cells[1].textContent).toBe('—');
  });

  test('a report from a server that does not price yet still opens without crashing', async () => {
    const old = { ...SMALL, usage: { ...SMALL.usage } };
    delete old.usage.cost;
    delete old.usage.unpriced_models;
    fetchUsage.mockResolvedValue({
      tasks: [old], totals: { ...old.usage }, uncounted_backends: [],
    });
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByText('Tiny fix')).toBeInTheDocument();
    expect(within(screen.getByRole('listitem')).getByText('10k')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('$');
  });

  test('a task with no summary still shows its id', async () => {
    fetchUsage.mockResolvedValue(report([{ ...task('UNA-9', '', { total: 5, output: 5 }), summary: '' }]));
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByText('No summary')).toBeInTheDocument();
    expect(screen.getByText('UNA-9')).toBeInTheDocument();
  });

  // ----- created / last used -----

  test('a row says when the task was created and when it was last used', async () => {
    const dated = task('UNA-20', 'Dated', {
      total: 100, output: 10, turns: 2, first_at: '2026-09-02T00:00:00Z', last_at: '2020-01-05T00:00:00Z',
    }, { created_at: '2026-09-01T08:30:00Z' });
    await open([dated]);
    const row = screen.getByRole('listitem');
    // The creation day is absolute; the last use is how long ago, with the exact stamp on hover.
    expect(within(row).getByText('created 2026-09-01')).toHaveAttribute('title', '2026-09-01T08:30:00Z');
    expect(within(row).getByText('last used 2020-01-05')).toHaveAttribute('title', '2020-01-05T00:00:00Z');
  });

  test('a task from a server that sends no creation date shows its first turn instead', async () => {
    const old = task('UNA-21', 'Older server', { total: 5, first_at: '2026-09-02T00:00:00Z' });
    await open([old]);
    expect(screen.getByText('created 2026-09-02')).toBeInTheDocument();
  });

  test('a task with no dates at all shows neither, rather than inventing them', async () => {
    await open([SMALL]);
    expect(screen.getByRole('listitem').textContent).not.toMatch(/created|last used/);
  });

  test('can be ordered by when the task was created', async () => {
    const early = task('UNA-30', 'Early', { total: 900 }, { created_at: '2026-08-01T00:00:00Z' });
    const late = task('UNA-31', 'Late', { total: 100 }, { created_at: '2026-10-01T00:00:00Z' });
    await open([early, late]);
    expect(rowOrder()).toEqual(['UNA-30', 'UNA-31']);
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'created' } });
    expect(rowOrder()).toEqual(['UNA-31', 'UNA-30']);
  });

  // ----- deleted tasks -----

  const GONE = task('UNA-40', 'Deleted long ago', {
    input: 0, output: 2_000, cache_read: 98_000, total: 100_000, turns: 12,
    first_at: '2026-09-01T00:00:00Z', last_at: '2026-09-09T00:00:00Z',
  }, { status: 'done', created_at: '2026-09-01T00:00:00Z', deleted_at: '2026-10-10T12:00:00Z', chats: 3 });

  function withHistory(live, deleted) {
    return {
      ...report(live),
      deleted_tasks: deleted,
      totals_with_deleted: report([...live, ...deleted]).totals,
    };
  }

  async function openWithHistory(live, deleted) {
    fetchUsage.mockResolvedValue(withHistory(live, deleted));
    const view = render(<UsageDialog onClose={() => {}} />);
    await screen.findByText(live[0].summary);
    return view;
  }

  const toggle = () => screen.getByRole('checkbox', { name: /Include deleted tasks/ });

  test('deleted tasks stay out until asked for, and the switch says how many there are', async () => {
    await openWithHistory([BIG], [GONE]);
    expect(rowOrder()).toEqual(['UNA-1']);
    expect(toggle()).not.toBeChecked();
    expect(toggle()).toBeEnabled();
    expect(screen.getByText('Include deleted tasks (1)')).toBeInTheDocument();
    // The totals are the live ones: 1 task, not 2.
    expect(document.querySelector('.usage-tile-hint')).toHaveTextContent('1 task ·');
  });

  test('turning it on lists them, marked deleted, and counts them in the totals', async () => {
    const { container } = await openWithHistory([BIG], [GONE]);

    fireEvent.click(toggle());

    expect(rowOrder()).toEqual(['UNA-1', 'UNA-40']);
    const gone = screen.getAllByRole('listitem')[1];
    expect(gone).toHaveTextContent('Deleted long ago');
    expect(within(gone).getByText('deleted 2026-10-10')).toHaveAttribute('title', 'Deleted 2026-10-10T12:00:00Z');
    expect(gone).toHaveTextContent('created 2026-09-01');
    expect(gone).toHaveTextContent('last used 2026-09-09');
    // A deleted task's status is no longer a live one — it says "deleted" instead.
    expect(gone.textContent).not.toContain('done');
    const tiles = container.querySelectorAll('.usage-tile');
    expect(tiles[0].textContent).toContain('2 tasks (1 deleted)');
    // 1,477,040,000 + 100,000 — the server's total with them, not one summed here.
    expect(tiles[0].querySelector('.usage-tile-value')).toHaveAttribute('title', '1,477,140,000');
    expect(document.querySelector('.usage-tile-hint')).toHaveTextContent('2 tasks (1 deleted)');
  });

  test('a deleted task opens to its kept figures and says they were counted when it was deleted', async () => {
    await openWithHistory([BIG], [GONE]);
    fireEvent.click(toggle());
    fireEvent.click(screen.getByRole('button', { name: /UNA-40/ }));
    expect(screen.getByText('3 chats counted when the task was deleted (2026-10-10).')).toBeInTheDocument();
  });

  test('a deleted task and a live one with the same id open independently', async () => {
    const again = { ...GONE, task_id: 'UNA-1', summary: 'Same id, deleted earlier' };
    await openWithHistory([BIG], [again]);
    fireEvent.click(toggle());
    const [live, deleted] = screen.getAllByRole('button', { name: /UNA-1/ });
    fireEvent.click(deleted);
    expect(deleted).toHaveAttribute('aria-expanded', 'true');
    expect(live).toHaveAttribute('aria-expanded', 'false');
  });

  test('the choice is remembered for the next time the dialog opens', async () => {
    const first = await openWithHistory([BIG], [GONE]);
    fireEvent.click(toggle());
    first.unmount();

    await openWithHistory([BIG], [GONE]);
    expect(toggle()).toBeChecked();
    expect(rowOrder()).toEqual(['UNA-1', 'UNA-40']);

    fireEvent.click(toggle());
    expect(rowOrder()).toEqual(['UNA-1']);
  });

  test('with nothing deleted on record the switch is there but cannot be turned on, and says why', async () => {
    await openWithHistory([BIG], []);
    expect(toggle()).toBeDisabled();
    expect(toggle()).not.toBeChecked();
    expect(screen.getByText('Include deleted tasks (0)').closest('label')).toHaveAttribute(
      'title', expect.stringContaining('No deleted task is on record yet'),
    );
  });

  test('a remembered "on" with nothing to show does not claim to be on', async () => {
    const first = await openWithHistory([BIG], [GONE]);
    fireEvent.click(toggle());
    first.unmount();
    await openWithHistory([BIG], []);
    expect(toggle()).not.toBeChecked();
    expect(document.querySelector('.usage-tile-hint')).not.toHaveTextContent('deleted)');
  });

  test('when only deleted tasks have usage, the list says to look at them', async () => {
    fetchUsage.mockResolvedValue(withHistory([], [GONE]));
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByText(/No current task has used any tokens/)).toHaveTextContent(
      'Tick "Include deleted tasks" to see 1 deleted.',
    );
    fireEvent.click(toggle());
    expect(await screen.findByText('Deleted long ago')).toBeInTheDocument();
  });

  test('an older server that sends no history still shows its tasks, with the switch off', async () => {
    await open([BIG]);
    expect(toggle()).toBeDisabled();
    expect(rowOrder()).toEqual(['UNA-1']);
  });

  test('the footnote says what is kept of a deleted task, and what is not', async () => {
    await open([BIG]);
    expect(document.querySelector('.usage-foot')).toHaveTextContent(
      "kato keeps only that task's totals — numbers, never the conversation",
    );
  });
});
