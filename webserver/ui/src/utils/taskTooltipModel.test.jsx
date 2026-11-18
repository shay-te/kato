import { describe, test, expect } from 'vitest';
import { taskTooltipModelFor } from './taskTooltipModel.js';

// A live, idle chat: a state the card draws a badge for.
const idle = (extra = {}) => ({ task_id: 'T1', live: true, working: false, ...extra });

describe('the task card names the agent the task runs on', () => {
  test('a Codex task\'s badge says Codex, a Claude task\'s says Claude', () => {
    // It said "Claude" on every task, Codex ones too.
    expect(taskTooltipModelFor(idle({ agent_backend: 'codex' })).agentBadge.label).toMatch(/^Codex: /);
    expect(taskTooltipModelFor(idle({ agent_backend: 'claude' })).agentBadge.label).toMatch(/^Claude: /);
  });

  test('an agent kato could not name is "Agent", never a guess', () => {
    expect(taskTooltipModelFor(idle()).agentBadge.label).toMatch(/^Agent: /);
  });

  test('the caption says how the trigger relates to the task', () => {
    expect(taskTooltipModelFor(idle(), null, false, 'Review loop of').caption).toBe('Review loop of');
    expect(taskTooltipModelFor(idle()).caption).toBeUndefined();
  });
});

describe('the task card says where its ticket is', () => {
  test('a Ticket row with its status, none while it is not known', () => {
    const rows = (session) => taskTooltipModelFor(session, null, false).rows;
    expect(rows(idle({ ticket_state: 'In Review' }))).toContainEqual({ label: 'Ticket', value: 'In Review' });
    expect(rows(idle()).some((row) => row.label === 'Ticket')).toBe(false);
  });
});
