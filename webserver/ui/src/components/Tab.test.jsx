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

  test('changes-pending indicator appears only when has_changes_pending is true', () => {
    const { container: c1 } = render(
      <Tab session={_session({ has_changes_pending: false })} onSelect={() => {}} />,
    );
    expect(c1.querySelector('.tab-changes-indicator')).toBeNull();

    const { container: c2 } = render(
      <Tab session={_session({ has_changes_pending: true })} onSelect={() => {}} />,
    );
    expect(c2.querySelector('.tab-changes-indicator')).toBeInTheDocument();
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

  test('the changes-indicator reserve is applied only when it is showing', () => {
    const { container: without } = render(
      <Tab session={_session({ has_changes_pending: false })} onSelect={() => {}} />,
    );
    expect(without.querySelector('li')).not.toHaveClass('has-changes');

    const { container: with_ } = render(
      <Tab session={_session({ has_changes_pending: true })} onSelect={() => {}} />,
    );
    expect(with_.querySelector('li')).toHaveClass('has-changes');
  });

  test('an un-resized tab is left to size itself to its name', () => {
    // No inline max-width, and no `has-custom-width` class: both of those
    // are what pinned the pill to a default width and ellipsised the ticket
    // name the operator was trying to read.
    window.localStorage.removeItem('kato.tab.cap.v3.KATO-123');
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} />,
    );
    const li = container.querySelector('li');

    expect(li.style.maxWidth).toBe('');
    expect(li).not.toHaveClass('has-custom-width');
  });

  test('a tab the operator resized takes a definite width, not a cap', () => {
    // Definite, because a max-width leaves an inline-block pill sizing to
    // its content — and the label's percentage reserve then resolves against
    // a width derived from the label, ellipsising a name that had room. No
    // amount of widening cleared it.
    window.localStorage.setItem('kato.tab.cap.v3.KATO-123', '320');
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} />,
      );
      const li = container.querySelector('li');

      expect(li.style.width).toBe('320px');
      expect(li.style.maxWidth).toBe('');
      expect(li).toHaveClass('has-custom-width');
    } finally {
      window.localStorage.removeItem('kato.tab.cap.v3.KATO-123');
    }
  });

  test('a width left over from the mount-persisting hook is ignored', () => {
    // Every browser has one of these for every task it ever opened: the old
    // hook wrote the default on mount, so "stored" did not mean "chosen".
    // Honouring them would pin every tab to 260px and ellipsise the name.
    window.localStorage.setItem('kato.tab.cap.KATO-123', '260');
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} />,
      );
      const li = container.querySelector('li');

      expect(li.style.maxWidth).toBe('');
      expect(li).not.toHaveClass('has-custom-width');
    } finally {
      window.localStorage.removeItem('kato.tab.cap.KATO-123');
    }
  });

  test('a stored width wider than the content is pulled back to it', () => {
    // Dragging past the natural width only adds empty pill, so the measured
    // auto-size width is the drag's upper bound. useResizable re-clamps when
    // that bound arrives, so an over-wide stored value corrects on load.
    const rect = window.HTMLElement.prototype.getBoundingClientRect;
    window.HTMLElement.prototype.getBoundingClientRect = function () {
      return { width: 300, height: 30, top: 0, left: 0, right: 0, bottom: 0 };
    };
    window.localStorage.setItem('kato.tab.cap.v3.KATO-123', '900');
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} />,
      );

      // 300 measured + 24px slack. The slack is generous on purpose: an
      // exact bound puts the ellipsis back at maximum width the moment any
      // of the chrome measurements shifts by a pixel.
      expect(container.querySelector('li').style.width).toBe('302px');
    } finally {
      window.localStorage.removeItem('kato.tab.cap.v3.KATO-123');
      window.HTMLElement.prototype.getBoundingClientRect = rect;
    }
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

  test('the pill keeps its width while renaming', () => {
    // A tab is inline-block, so it sizes to content — and renaming swaps the
    // id + name for one input. Without pinning, double-clicking shrank the
    // pill (and shoved every tab after it) the moment you started editing.
    // jsdom reports offsetWidth as 0, so stub a real measurement.
    const measured = Object.getOwnPropertyDescriptor(
      window.HTMLElement.prototype, 'offsetWidth',
    );
    Object.defineProperty(window.HTMLElement.prototype, 'offsetWidth', {
      configurable: true, value: 240,
    });
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} onRename={() => {}} />,
      );
      const li = container.querySelector('li');
      expect(li.style.width).toBe('');

      fireEvent.doubleClick(container.querySelector('.tab-label-title'));
      expect(li.style.width).toBe('240px');
      // The input is sized from the label it replaces, so its box can never
      // run on under the pin / × (which paint above it and would hide the
      // overflow). Same stub, less the 2px inset.
      expect(container.querySelector('.tab-label-rename').style.width)
        .toBe('238px');

      fireEvent.keyDown(container.querySelector('.tab-label-rename'), {
        key: 'Escape',
      });
      // Released afterwards — the pinned width must not outlive the edit, or
      // a later resize-drag would be fighting a stale inline width.
      expect(li.style.width).toBe('');
    } finally {
      Object.defineProperty(
        window.HTMLElement.prototype, 'offsetWidth', measured,
      );
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
