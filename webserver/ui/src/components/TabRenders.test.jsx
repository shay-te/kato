// One tab's change redraws one tab.
//
// Reported as "the tabs are refreshing like crazy, any small change elsewhere
// re-renders the entire screen". Measured on a live kato: every poll tick
// redrew every tab, with identical data, about once a second.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

const renders = vi.hoisted(() => ({ byTask: {} }));

// Called exactly once per Tab render (each row derives its agent status).
vi.mock('../utils/agentStatus.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    deriveAgentStatus: (session, ...rest) => {
      renders.byTask[session.task_id] = (renders.byTask[session.task_id] || 0) + 1;
      return actual.deriveAgentStatus(session, ...rest);
    },
  };
});

import TabList from './TabList.jsx';
import { reuseUnchanged } from '../utils/reuseUnchanged.js';

const session = (id, extra = {}) => ({
  task_id: id, task_summary: `Task ${id}`, status: 'active', ...extra,
});
const noop = () => {};
const stableProps = {
  sessionsLoaded: true,
  katoReachable: true,
  activeTaskId: 'T-1',
  attentionTaskIds: new Set(),
  agentStatuses: {},
  revealRequestId: 0,
  onSelect: noop,
  onForget: noop,
  onOpenAddTask: noop,
  onNewTask: noop,
  onOpenTaskPalette: noop,
  onScanNow: noop,
  scanPending: false,
  onVisibleOrderChange: noop,
  onOpenBulkForget: noop,
};
const list = (sessions) => <TabList {...stableProps} sessions={sessions} />;
const poll = (held, fresh) => reuseUnchanged(held, fresh, (s) => s.task_id);

describe('TabList — what a poll redraws', () => {
  beforeEach(() => { renders.byTask = {}; });

  test('a poll with identical data redraws no tab', () => {
    const held = [session('T-1'), session('T-2'), session('T-3')];
    const { rerender } = render(list(held));
    renders.byTask = {};
    rerender(list(poll(held, [session('T-1'), session('T-2'), session('T-3')])));
    expect(renders.byTask).toEqual({});
  });

  test('one session changing redraws only that tab', () => {
    const held = [session('T-1'), session('T-2'), session('T-3')];
    const { rerender } = render(list(held));
    renders.byTask = {};
    rerender(list(poll(held, [session('T-1'), session('T-2', { working: true }), session('T-3')])));
    expect(Object.keys(renders.byTask)).toEqual(['T-2']);
  });

  test('selecting a task redraws only the two tabs whose active state changed', () => {
    const held = [session('T-1'), session('T-2'), session('T-3')];
    const { rerender } = render(list(held));
    renders.byTask = {};
    rerender(<TabList {...stableProps} sessions={held} activeTaskId="T-2" />);
    expect(Object.keys(renders.byTask).sort()).toEqual(['T-1', 'T-2']);
  });
});
