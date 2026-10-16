// Tests for UsageDialog — how many tokens each task has consumed.
//
// The dialog's one job is to keep three numbers apart that a lazy sum would
// blur together: what the model WROTE, what was sent to it NEW, and the
// context it RE-READ on every turn. Cache reads are usually >90% of the raw
// total; folding them into "input" would make every task look like it fed the
// model a billion tokens of new material.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchUsage: vi.fn(),
}));

import { fetchUsage } from '../api.js';
import UsageDialog from './UsageDialog.jsx';

function usage(overrides = {}) {
  return {
    input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0, turns: 0,
    first_at: '', last_at: '', models: {}, ...overrides,
  };
}

function task(id, summary, usageOverrides, extra = {}) {
  return {
    task_id: id, summary, status: 'active', chats: 1, uncounted_backends: [],
    usage: usage(usageOverrides), ...extra,
  };
}

const BIG = task('UNA-1', 'Big refactor', {
  input: 40_000, output: 7_000_000, cache_read: 1_400_000_000, cache_write: 70_000_000,
  total: 1_477_040_000, turns: 3015, last_at: '2026-10-16T03:00:00Z',
  models: {
    'claude-opus-5': {
      input: 40_000, output: 7_000_000, cache_read: 1_400_000_000, cache_write: 70_000_000,
      total: 1_477_040_000, turns: 3015,
    },
  },
});
const SMALL = task('UNA-2', 'Tiny fix', {
  input: 100, output: 900, cache_read: 9_000, cache_write: 0, total: 10_000, turns: 4,
});

function report(tasks, extra = {}) {
  const totals = tasks.reduce((sum, t) => usage({
    input: sum.input + t.usage.input,
    output: sum.output + t.usage.output,
    cache_read: sum.cache_read + t.usage.cache_read,
    cache_write: sum.cache_write + t.usage.cache_write,
    total: sum.total + t.usage.total,
    turns: sum.turns + t.usage.turns,
  }), usage());
  return { tasks, totals, uncounted_backends: [], ...extra };
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
});

describe('UsageDialog', () => {
  test('lists every task with its total, biggest first', async () => {
    await open([SMALL, BIG]);
    expect(rowOrder()).toEqual(['UNA-1', 'UNA-2']);
    expect(screen.getByText('Big refactor')).toBeInTheDocument();
    // 1.477B reads as billions, not "1477M".
    expect(screen.getAllByText('1.5B').length).toBeGreaterThan(0);
  });

  test('keeps output, new input and cache reads apart in the summary tiles', async () => {
    const { container } = await open([BIG]);
    const tiles = container.querySelectorAll('.usage-tile');
    expect(tiles).toHaveLength(4);
    expect(tiles[1].textContent).toContain('Written by the model');
    expect(tiles[1].textContent).toContain('7.0M');
    // "New input" is input + cache writes — 40k + 70M — and not the cache reads.
    expect(tiles[2].textContent).toContain('70M');
    expect(tiles[3].textContent).toContain('1.4B');
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
    expect(screen.queryByText('claude-opus-5')).toBeNull();

    fireEvent.click(head);

    expect(head).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('claude-opus-5')).toBeInTheDocument();
    expect(screen.getByText('1 chat counted.')).toBeInTheDocument();

    fireEvent.click(head);
    expect(screen.queryByText('claude-opus-5')).toBeNull();
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

  test('a task with no summary still shows its id', async () => {
    fetchUsage.mockResolvedValue(report([{ ...task('UNA-9', '', { total: 5, output: 5 }), summary: '' }]));
    render(<UsageDialog onClose={() => {}} />);
    expect(await screen.findByText('No summary')).toBeInTheDocument();
    expect(screen.getByText('UNA-9')).toBeInTheDocument();
  });
});
