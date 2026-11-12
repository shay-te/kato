// Tests for Tab. Renders one task tab in the sidebar list: task id,
// summary, status dot, optional commit indicator, forget (X) button.
// onSelect fires on click; onForget fires on X click (after a
// window.confirm). The active prop drives styling.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import Tab from './Tab.jsx';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';


function _session(overrides = {}) {
  return {
    task_id: 'KATO-123',
    task_summary: 'Fix the bug',
    status: TAB_STATUS.ACTIVE,
    working: true,
    has_changes_pending: false,
    live: true,
    [AGENT_SESSION_ID]: 'sess-1',
    ...overrides,
  };
}


describe('Tab', () => {

  test('pill shows the task id AND the task name (VSCode-style title)', () => {
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} />,
    );
    expect(screen.getByText('KATO-123')).toBeInTheDocument();
    // The name is now shown IN the pill (ellipsised at the tab's
    // max-width) so the operator can tell tabs apart without hovering.
    // The full name still lives in the hover card too.
    expect(container.querySelector('.tab-label-title')).toHaveTextContent('Fix the bug');
    // Still no card mounted until hover.
    expect(document.querySelector('.tab-tooltip')).toBeNull();
  });

  test('clicking the tab fires onSelect with the task id', () => {
    const onSelect = vi.fn();
    render(<Tab session={_session()} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('KATO-123'));
    expect(onSelect).toHaveBeenCalledWith('KATO-123');
  });

  test('active prop adds the active class', () => {
    const { container } = render(
      <Tab session={_session()} active={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('active');
  });

  test('needsAttention prop adds the needs-attention class', () => {
    const { container } = render(
      <Tab session={_session()} needsAttention={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('needs-attention');
  });

  test('a running review loop rings the pill, without touching the status dot', () => {
    const running = _session({
      review_loop: { loop_id: 'a'.repeat(32), status: 'running', phase: 'reviewing', round: 1, max_rounds: 3 },
    });
    const { container, rerender } = render(<Tab session={running} onSelect={() => {}} />);
    const li = container.querySelector('li');
    expect(li).toHaveClass('has-review-loop');
    // The ring is a SEPARATE axis — the status dot keeps its own classes.
    const dotClass = container.querySelector('.status-dot')?.className;
    expect(dotClass).toBeTruthy();

    // A finished loop no longer rings the pill (the ring means "running").
    rerender(<Tab session={_session({
      review_loop: { loop_id: 'a'.repeat(32), status: 'clean', phase: 'done', round: 2, max_rounds: 3 },
    })} onSelect={() => {}} />);
    expect(container.querySelector('li')).not.toHaveClass('has-review-loop');
    // The status dot did not change because of the loop.
    expect(container.querySelector('.status-dot')?.className).toBe(dotClass);
  });

  test('status dot reflects the resolved status (attention overrides base)', () => {
    const { container } = render(
      <Tab
        session={_session({ status: TAB_STATUS.ACTIVE, working: false })}
        needsAttention={true}
        onSelect={() => {}}
      />,
    );
    // resolveTabStatus → ATTENTION when needsAttention is true.
    expect(container.querySelector('.status-dot')).toHaveClass(`status-${TAB_STATUS.ATTENTION}`);
  });

  test('working session paints the working status dot', () => {
    const { container } = render(
      <Tab session={_session({ status: TAB_STATUS.REVIEW, working: true })} onSelect={() => {}} />,
    );
    const dot = container.querySelector('.status-dot');
    expect(dot).not.toHaveClass('is-working');
    expect(dot).toHaveClass(`status-${TAB_STATUS.WORKING}`);
    expect(dot).not.toHaveClass('is-idle-alive');
  });

  test('live working status paints the tab dot even when the session poll is stale', () => {
    const { container } = render(
      <Tab
        session={_session({ status: TAB_STATUS.REVIEW, working: false })}
        liveStatus={{ lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true }}
        onSelect={() => {}}
      />,
    );
    const dot = container.querySelector('.status-dot');
    expect(dot).toHaveClass(`status-${TAB_STATUS.WORKING}`);
    expect(dot).not.toHaveClass(`status-${TAB_STATUS.REVIEW}`);
  });

  test('non-working session has no is-working class', () => {
    const { container } = render(
      <Tab session={_session({ working: false })} onSelect={() => {}} />,
    );
    const dot = container.querySelector('.status-dot');
    expect(dot).not.toHaveClass('is-working');
    // working:false on an ACTIVE tab is the "idle but alive" state.
    expect(dot).toHaveClass('is-idle-alive');
  });


  test('clicking forget button requests forget via onForget(task_id)', () => {
    // No native confirm anymore — the hard-confirm lives in
    // ForgetTaskModal at App level. Tab just hands off the id.
    const onSelect = vi.fn();
    const onForget = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm');
    render(<Tab session={_session()} onSelect={onSelect} onForget={onForget} />);

    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onForget).toHaveBeenCalledWith('KATO-123');
    // event.stopPropagation in handleForget — onSelect must not fire.
    expect(onSelect).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });


  test('a row is as wide as the menu: no resize grip, no stored width', () => {
    const { container } = render(<Tab session={_session()} onSelect={() => {}} />);
    const li = container.querySelector('li');
    expect(container.querySelector('.tab-resize-handle')).toBeNull();
    expect(li.style.width).toBe('');
    expect(li).not.toHaveClass('has-custom-width');
  });

  test('a sub-task row under its main task is marked nested, without the sub-task icon', () => {
    // Its place under the main task already says whose it is.
    const session = _session({ task_id: 'LOCAL-4', plan_parent: 'KATO-123' });
    const { container, rerender } = render(<Tab session={session} onSelect={() => {}} nested />);
    expect(container.querySelector('li')).toHaveClass('is-nested');
    expect(screen.queryByRole('img', { name: /subtask of/i })).toBeNull();
    // Out of its group (its main task has no row here) it keeps the mark.
    rerender(<Tab session={session} onSelect={() => {}} />);
    expect(screen.getByRole('img', { name: 'Subtask of KATO-123' })).toBeInTheDocument();
  });

  test('a pencil button opens the rename box', () => {
    // The discoverable path. Double-click alone was a guess — nothing on the
    // tab said it could be renamed.
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /rename the kato-123 tab/i }));

    expect(container.querySelector('.tab-label-rename')).toBeInTheDocument();
  });

  test('the pencil does not also select the tab', () => {
    const onSelect = vi.fn();
    render(<Tab session={_session()} onSelect={onSelect} onRename={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /rename the kato-123 tab/i }));

    expect(onSelect).not.toHaveBeenCalled();
  });

  test('the pencil gives way to the input while renaming', () => {
    // It would otherwise sit next to its own text box, doing nothing.
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /rename the kato-123 tab/i }));

    expect(container.querySelector('.tab-rename-btn')).toBeNull();
  });

  test('no pencil when renaming is not wired', () => {
    const { container } = render(<Tab session={_session()} onSelect={() => {}} />);

    expect(container.querySelector('.tab-rename-btn')).toBeNull();
  });

  test('double-clicking the name opens a rename box holding the current name', () => {
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    const input = container.querySelector('.tab-label-rename');
    expect(input).toBeInTheDocument();
    expect(input.value).toBe('Fix the bug');
  });

  test('the rename box selects its text so typing REPLACES the name', () => {
    // The append bug: autoFocus parks the caret at the end of a value that
    // is mostly clipped out of sight, so an operator who retypes the name
    // gets "Core-lib skillCore-lib skillCore-lib skill" instead of one copy.
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    const input = container.querySelector('.tab-label-rename');
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('Fix the bug'.length);
  });

  test('the id makes way for the rename box instead of crowding it', () => {
    // Sharing the label's ~158px with the id left about 90px of a 160px
    // input visible, the rest clipped — which is what hid the text being
    // edited in the first place.
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
    );
    expect(container.querySelector('.tab-label.is-renaming')).toBeNull();

    fireEvent.doubleClick(container.querySelector('.tab-label-title'));
    expect(container.querySelector('.tab-label.is-renaming')).toBeInTheDocument();
  });

  test('a fractional label width is not rounded up into a clipped border', () => {
    // offsetWidth rounds to whole pixels. Rounding UP makes the input a hair
    // wider than the label holding it, and the label's overflow:hidden then
    // shaves the input's 1px right border — the "text field is cut off on
    // the right" report. The sub-pixel measurement has to win.
    const rect = window.HTMLElement.prototype.getBoundingClientRect;
    window.HTMLElement.prototype.getBoundingClientRect = function () {
      return { width: 239.6, height: 30, top: 0, left: 0, right: 0, bottom: 0 };
    };
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
      );
      fireEvent.click(
        screen.getByRole('button', { name: /rename the kato-123 tab/i }),
      );

      // Sub-pixel width kept (not rounded to 240), less the 2px inset that
      // keeps the box off its container's edge.
      expect(container.querySelector('.tab-label-rename').style.width)
        .toBe('237.6px');
    } finally {
      window.HTMLElement.prototype.getBoundingClientRect = rect;
    }
  });

  test('Enter commits the new name', () => {
    const onRename = vi.fn();
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={onRename} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    const input = container.querySelector('.tab-label-rename');
    fireEvent.change(input, { target: { value: 'Core-lib skill' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onRename).toHaveBeenCalledWith('KATO-123', 'Core-lib skill');
    expect(container.querySelector('.tab-label-rename')).toBeNull();
  });

  test('a blank name clears the override', () => {
    const onRename = vi.fn();
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={onRename} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    const input = container.querySelector('.tab-label-rename');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onRename).toHaveBeenCalledWith('KATO-123', '');
  });

  test('Escape closes the rename box without renaming', () => {
    const onRename = vi.fn();
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} onRename={onRename} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    const input = container.querySelector('.tab-label-rename');
    fireEvent.change(input, { target: { value: 'discard me' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(onRename).not.toHaveBeenCalled();
    expect(container.querySelector('.tab-label-rename')).toBeNull();
  });

  test('double-clicking the name does not also select the tab', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <Tab session={_session()} onSelect={onSelect} onRename={() => {}} />,
    );
    fireEvent.doubleClick(container.querySelector('.tab-label-title'));

    expect(onSelect).not.toHaveBeenCalled();
  });

  test('forget button is a no-op when onForget is not a function', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    // Should not throw — handleForget bails when typeof
    // onForget !== 'function'.
    expect(() =>
      fireEvent.click(screen.getByLabelText('Forget this task')),
    ).not.toThrow();
  });

  test('missing task_summary renders without crashing', () => {
    const { container } = render(
      <Tab session={_session({ task_summary: null })} onSelect={() => {}} />,
    );
    // No summary <p> in the pill anymore; id still shows.
    expect(container.querySelector('li')).toBeInTheDocument();
    expect(screen.getByText('KATO-123')).toBeInTheDocument();
  });

  test('hover shows the designed tooltip card after the delay', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <Tab
          session={_session({ branch_name: 'feature/x' })}
          onSelect={() => {}}
        />,
      );
      const li = container.querySelector('li');
      fireEvent.mouseEnter(li);
      // Nothing before the hover delay elapses.
      expect(document.querySelector('.tab-tooltip')).toBeNull();
      act(() => { vi.advanceTimersByTime(400); });
      const card = document.querySelector('.tab-tooltip');
      expect(card).toBeInTheDocument();
      // Card carries the structured facts: id, summary, a Branch row.
      expect(card).toHaveTextContent('KATO-123');
      expect(card).toHaveTextContent('Fix the bug');
      expect(card).toHaveTextContent('Branch');
      expect(card).toHaveTextContent('feature/x');
      // Mouse leave tears the card down.
      fireEvent.mouseLeave(li);
      expect(document.querySelector('.tab-tooltip')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  test('leaving before the delay never opens the card', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} />,
      );
      const li = container.querySelector('li');
      fireEvent.mouseEnter(li);
      fireEvent.mouseLeave(li);
      act(() => { vi.advanceTimersByTime(400); });
      expect(document.querySelector('.tab-tooltip')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});


describe('Tab — pin button', () => {

  test('renders the pin button on every tab', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(screen.getByRole('button', { name: /pin this task/i })).toBeInTheDocument();
  });

  test('unpinned state advertises "Pin" + aria-pressed=false', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    const btn = screen.getByRole('button', { name: /pin this task/i });
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    expect(btn).not.toHaveClass('is-pinned');
  });

  test('pinned state advertises "Unpin" + aria-pressed=true + is-pinned class', () => {
    render(<Tab session={_session()} pinned={true} onSelect={() => {}} />);
    const btn = screen.getByRole('button', { name: /unpin this task/i });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(btn).toHaveClass('is-pinned');
  });

  test('pinned prop adds is-pinned class to the <li>', () => {
    const { container } = render(
      <Tab session={_session()} pinned={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('is-pinned');
  });

  test('clicking the pin button fires onTogglePin with the task id', () => {
    const onTogglePin = vi.fn();
    const onSelect = vi.fn();
    render(
      <Tab
        session={_session()}
        onSelect={onSelect}
        onTogglePin={onTogglePin}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /pin this task/i }));
    expect(onTogglePin).toHaveBeenCalledWith('KATO-123');
    // Must NOT also fire onSelect — pin button is its own action,
    // not a tab-activation click.
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('pin click without an onTogglePin handler is a safe no-op', () => {
    // Don't crash when the host forgets to wire the handler — just
    // swallow the click.
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(() => {
      fireEvent.click(screen.getByRole('button', { name: /pin this task/i }));
    }).not.toThrow();
  });
});


// ── The × must delete, never select ───────────────────────────────────────
// Operator report: "when I press the cross icon he will not show me the
// dialog, even when I press it 5-6 times, and he also switches to the task
// which I did not want."
//
// Cause: `.tabs-pane-side .tab:active { transform: scale(0.97) }` scaled the
// whole <li> about its centre on mouse-DOWN, sliding the × disc LEFT out from
// under the cursor — 0.03 x (halfWidth - 20px), i.e. ~9px on a 665px tab and
// ~13px on a 909px one, against a disc only 16px wide. Tabs size to the ticket
// summary, so real ones are wide. Mouse-UP then hit-tested the <li>, and the
// browser dispatched `click` at the nearest common ancestor — the <li>, whose
// handler selects the task.
//
// jsdom does not do layout, so the SHIFT itself cannot be reproduced here.
// What is pinned instead is the consequence and the guard: a click that
// arrives on the <li> after a press that began on the × must still forget.

describe('Tab — the × deletes and never selects', () => {
  function renderTab(extra = {}) {
    const onSelect = vi.fn();
    const onForget = vi.fn();
    const { container } = render(
      <Tab
        session={_session({ task_id: 'UNA-1' })}
        onSelect={onSelect}
        onForget={onForget}
        {...extra}
      />,
    );
    return { container, onSelect, onForget };
  }

  test('a normal × click forgets and does not select', () => {
    const { onSelect, onForget } = renderTab();
    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(onForget).toHaveBeenCalledWith('UNA-1');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('a press that starts on the × still forgets when the click retargets', () => {
    // Exactly the reported failure: pointerdown lands on the ×, the element
    // moves, and the click is dispatched at the <li> instead.
    const { container, onSelect, onForget } = renderTab();
    fireEvent.pointerDown(screen.getByLabelText('Forget this task'));
    fireEvent.click(container.querySelector('li.tab'));

    expect(onForget).toHaveBeenCalledWith('UNA-1');
    expect(onSelect).not.toHaveBeenCalled();   // the task must NOT open
  });

  test('the intent is consumed, so the next tab click selects normally', () => {
    const { container, onSelect, onForget } = renderTab();
    fireEvent.pointerDown(screen.getByLabelText('Forget this task'));
    fireEvent.click(container.querySelector('li.tab'));
    onForget.mockClear();

    fireEvent.click(container.querySelector('li.tab'));

    expect(onSelect).toHaveBeenCalledWith('UNA-1');
    expect(onForget).not.toHaveBeenCalled();
  });

  test('an ordinary click on the tab body still selects', () => {
    const { container, onSelect, onForget } = renderTab();
    fireEvent.click(container.querySelector('li.tab'));
    expect(onSelect).toHaveBeenCalledWith('UNA-1');
    expect(onForget).not.toHaveBeenCalled();
  });
});


describe('Tab — the dot while the review loop reviews', () => {
  test('a quiet chat with its reviewer reading the change shows the reviewing dot', () => {
    const reviewing = _session({
      working: false, review_loop: { status: 'running', phase: 'reviewing', round: 2, max_rounds: 5 },
    });
    const { container } = render(<Tab session={reviewing} onSelect={() => {}} />);
    expect(container.querySelector('.status-dot')).toHaveClass(`status-${TAB_STATUS.REVIEWING}`);
  });

  test('when the chat is fixing, the dot is the chat\'s again', () => {
    const fixing = _session({ review_loop: { status: 'running', phase: 'awaiting_fix', round: 2, max_rounds: 5 } });
    const { container } = render(<Tab session={fixing} onSelect={() => {}} />);
    expect(container.querySelector('.status-dot')).not.toHaveClass(`status-${TAB_STATUS.REVIEWING}`);
  });
});


describe('Tab — a plan\'s tasks', () => {
  const SUBTASK = 'Subtask of UNA-3267: Event registration';

  test('a sub-task shows its parent as a fixed mark, and only the name as its label', () => {
    const { container } = render(
      <Tab session={_session({ task_id: 'UNA-3285', task_summary: SUBTASK })} onSelect={() => {}} />,
    );
    expect(screen.getByRole('img', { name: 'Subtask of UNA-3267' })).toBeInTheDocument();
    expect(container.querySelector('.tab-label-title')).toHaveTextContent(/^Event registration$/);
  });

  test('renaming a sub-task edits the name only; the mark stays its own', () => {
    const onRename = vi.fn();
    const session = _session({ task_id: 'UNA-3285', task_summary: SUBTASK });
    const { container, rerender } = render(<Tab session={session} onSelect={() => {}} onRename={onRename} />);
    fireEvent.click(screen.getByRole('button', { name: /rename the una-3285 tab/i }));
    const input = container.querySelector('.tab-label-rename');
    expect(input).toHaveValue('Event registration');
    fireEvent.change(input, { target: { value: 'Sign-up for events' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onRename).toHaveBeenCalledWith('UNA-3285', 'Sign-up for events');
    // Renamed: still a sub-task of UNA-3267 (the mark reads the ticket, not the name).
    rerender(<Tab session={session} displayName="Sign-up for events" onSelect={() => {}} onRename={onRename} />);
    expect(screen.getByRole('img', { name: 'Subtask of UNA-3267' })).toBeInTheDocument();
    expect(container.querySelector('.tab-label-title')).toHaveTextContent(/^Sign-up for events$/);
  });

  test('an ordinary task has no sub-task mark', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(screen.queryByRole('img', { name: /subtask of/i })).toBeNull();
  });

  test('the task a plan was split from: its row says whether the split is done, and its card says where it is', () => {
    vi.useFakeTimers();
    try {
      const plan = { plan_id: 'p', status: 'running', step_count: 17, counts: { satisfied: 3 } };
      const { container, rerender } = render(
        <Tab session={_session({ plan_queue: plan })} onSelect={() => {}} />,
      );
      expect(screen.getByRole('img', { name: 'Split plan: Running · 3/17 done' }))
        .toHaveClass('tab-plan-queue-badge', 'is-running');
      fireEvent.mouseEnter(container.querySelector('li'));
      act(() => { vi.advanceTimersByTime(400); });
      expect(document.querySelector('.tab-tooltip')).toHaveTextContent('Split planRunning · 3/17 done');
      fireEvent.mouseLeave(container.querySelector('li'));
      rerender(<Tab session={_session({ plan_queue: null, plan_queue_finished: true })} onSelect={() => {}} />);
      expect(screen.getByRole('img', { name: /^Split plan: Finished/ })).toHaveClass('is-good');
      rerender(<Tab session={_session({ plan_queue: null })} onSelect={() => {}} />);
      expect(container.querySelector('.tab-plan-queue-badge')).toBeNull();  // no plan, no mark
    } finally {
      vi.useRealTimers();
    }
  });

  test('the row says whether its review loop is done: running, passed, or ended without passing', () => {
    const loop = {
      loop_id: 'c'.repeat(32), status: 'running', phase: 'reviewing', round: 1, max_rounds: 5,
      started_at: Date.now() / 1000,
    };
    const { container, rerender } = render(<Tab session={_session({ review_loop: loop })} onSelect={() => {}} />);
    const running = screen.getByRole('img', { name: 'Review loop: running' });
    expect(running).toHaveClass('tab-review-loop-badge', 'is-running');
    expect(running.querySelector('[data-icon="loop"]')).toHaveClass('kato-icon-spin');
    rerender(<Tab session={_session({ review_loop: { ...loop, status: 'clean' } })} onSelect={() => {}} />);
    const clean = screen.getByRole('img', { name: 'Review loop: clean' });
    expect(clean).toHaveClass('is-good');
    expect(clean.querySelector('.kato-icon-spin')).toBeNull();
    rerender(<Tab session={_session({ review_loop: { ...loop, status: 'stuck' } })} onSelect={() => {}} />);
    expect(screen.getByRole('img', { name: 'Review loop: stuck' })).toHaveClass('is-warn');
    rerender(<Tab session={_session({ review_loop: null })} onSelect={() => {}} />);
    expect(container.querySelector('.tab-review-loop-badge')).toBeNull();
  });

  test('the marks are not buttons: the toolbar opens the loop and the plan, a click on a mark opens the task', () => {
    const onSelect = vi.fn();
    const loop = { loop_id: 'd'.repeat(32), status: 'clean', round: 2, max_rounds: 5 };
    render(
      <Tab session={_session({ review_loop: loop, plan_queue_finished: true })} onSelect={onSelect} />,
    );
    expect(screen.queryByRole('button', { name: /review loop|split plan|plan board/i })).toBeNull();
    fireEvent.click(screen.getByRole('img', { name: 'Review loop: clean' }));
    expect(onSelect).toHaveBeenCalledWith('KATO-123');
  });
});
