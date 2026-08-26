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

// The assistant bubble carries the AGENT'S name. It was the constant
// 'Claude', so Codex's own reply — in Codex's own tab — was attributed to
// Claude. Reported as "no claude . codex!".
describe('EventLog — the assistant bubble names the agent', () => {
  function replyIn(agentName) {
    return render(
      <EventLog
        taskId="T1"
        agentName={agentName}
        entries={[{
          id: 'e0', source: 'stream', received_at_epoch: 1,
          raw: {
            type: 'item.completed',
            item: { type: 'agent_message', text: 'Hello back!' },
          },
        }]}
      />,
    );
  }

  test('a Codex reply is labelled Codex', () => {
    const { container } = replyIn('Codex');
    expect(container.querySelector('.bubble.assistant .bubble-label').textContent)
      .toBe('Codex');
  });

  test('a Claude reply is labelled Claude', () => {
    const { container } = replyIn('Claude');
    expect(container.querySelector('.bubble.assistant .bubble-label').textContent)
      .toBe('Claude');
  });

  test('the session-started bubble names the agent too', () => {
    const { container } = render(
      <EventLog
        taskId="T1"
        agentName="Codex"
        entries={[{
          id: 'e0', source: 'stream', received_at_epoch: 1,
          raw: { type: 'system', subtype: 'init', agent_session_id: 'abc12345' },
        }]}
      />,
    );
    expect(container.textContent).toContain('Codex session started');
    expect(container.textContent).not.toContain('Claude session started');
  });
});

// The operator's own prompt in a Codex transcript. ``codex exec`` takes it on
// stdin and never echoes it, so the transport records it as a ``user`` event
// in the SAME wire shape the other backend uses — which means this renderer
// needs no Codex-specific branch for it, and the reply-then-question ordering
// survives a page reload.
describe('EventLog — the operator’s prompt in a Codex chat', () => {
  function transcript() {
    return render(
      <EventLog
        taskId="T1"
        agentName="Codex"
        entries={[
          {
            id: 'e0', source: 'stream', received_at_epoch: 1,
            raw: {
              type: 'user',
              message: { content: [{ type: 'text', text: 'review my changes' }] },
            },
          },
          {
            id: 'e1', source: 'stream', received_at_epoch: 2,
            raw: {
              type: 'item.completed',
              item: { type: 'agent_message', text: 'Looks good.' },
            },
          },
        ]}
      />,
    );
  }

  test('the prompt is shown, not just the reply', () => {
    transcript();
    expect(screen.getByText(/review my changes/)).toBeTruthy();
  });

  test('the reply is still shown alongside it', () => {
    transcript();
    expect(screen.getByText('Looks good.')).toBeTruthy();
  });

  test('the prompt renders through the shared user path, not a Codex branch', () => {
    const { container } = transcript();
    // Same sticky-prompt treatment a typed message gets on the other
    // backend — one wire shape, one renderer.
    expect(container.querySelector('.chat-sticky-prompt')).toBeTruthy();
  });
});
