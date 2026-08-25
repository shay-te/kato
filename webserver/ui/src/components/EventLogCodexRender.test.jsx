/**
 * A Codex turn must render as a CONVERSATION, not as its wire events.
 *
 * Reported as "so hard to say hello back": a working Codex turn showed four
 * grey chips reading ``thread.started`` / ``turn.started`` /
 * ``item.completed`` / ``turn.completed`` — the event TYPE NAMES — while the
 * reply itself, which sits inside the item, was never displayed. The chat had
 * no Codex vocabulary at all, so everything fell through the unknown-event
 * fallback that prints ``raw.type``.
 */
import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import EventLog from './EventLog.jsx';

function show(events) {
  return render(
    <EventLog
      taskId="T1"
      entries={events.map((raw, i) => ({
        id: `e${i}`, source: 'stream', raw, received_at_epoch: 1,
      }))}
    />,
  );
}

const TURN = [
  { type: 'thread.started', thread_id: 't1' },
  { type: 'turn.started' },
  {
    type: 'item.completed',
    item: { id: 'i0', type: 'agent_message', text: 'Hello back!' },
  },
  { type: 'turn.completed', usage: { input_tokens: 10, output_tokens: 2 } },
];

describe('EventLog — Codex turn rendering', () => {
  test('the agent’s reply is shown', () => {
    show(TURN);
    expect(screen.getByText('Hello back!')).toBeTruthy();
  });

  test('no raw event names leak into the transcript', () => {
    const { container } = show(TURN);
    const text = container.textContent;
    for (const noise of [
      'thread.started', 'turn.started', 'turn.completed', 'item.completed',
    ]) {
      expect(text).not.toContain(noise);
    }
  });

  test('the reply renders as markdown, not raw text', () => {
    const { container } = show([{
      type: 'item.completed',
      item: { type: 'agent_message', text: '# Heading\n\n- one\n- two' },
    }]);
    expect(container.querySelector('h1')).toBeTruthy();
    expect(container.querySelectorAll('li').length).toBe(2);
  });

  test('a command the agent ran is shown as tool activity', () => {
    const { container } = show([{
      type: 'item.completed',
      item: { type: 'command_execution', command: 'npm test' },
    }]);
    expect(container.textContent).toContain('$ npm test');
  });

  test('file edits are named', () => {
    const { container } = show([{
      type: 'item.completed',
      item: { type: 'file_change', changes: [{ path: 'src/a.js' }, { path: 'b.js' }] },
    }]);
    expect(container.textContent).toContain('edited src/a.js, b.js');
  });

  test('reasoning traces stay hidden, like Claude’s', () => {
    const { container } = show([{
      type: 'item.completed',
      item: { type: 'reasoning', text: 'thinking out loud' },
    }]);
    expect(container.textContent).not.toContain('thinking out loud');
  });

  test('a turn failure is surfaced as an error', () => {
    const { container } = show([
      { type: 'turn.failed', error: { message: 'model refused' } },
    ]);
    expect(container.textContent).toContain('model refused');
  });

  test('a standalone error event is surfaced', () => {
    const { container } = show([{ type: 'error', message: 'rate limited' }]);
    expect(container.textContent).toContain('rate limited');
  });

  test('Claude events are untouched by the Codex branch', () => {
    show([{
      type: 'assistant',
      message: { content: [{ type: 'text', text: 'Claude speaking' }] },
    }]);
    expect(screen.getByText('Claude speaking')).toBeTruthy();
  });
});
