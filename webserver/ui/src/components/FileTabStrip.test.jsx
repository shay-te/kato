// Tests for FileTabStrip — the VS Code-style row of open-file tabs
// above the centre editor/diff pane. Renders nothing with no open
// tabs; otherwise one pill per tab (file/diff icon, basename label,
// close button); click selects, close button closes, middle-click
// closes, Ctrl/Cmd+W closes the focused tab.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import FileTabStrip from './FileTabStrip.jsx';


function tab(overrides = {}) {
  return {
    key: 'client::src/auth.py',
    taskId: 'T1',
    absolutePath: '/wks/T1/client/src/auth.py',
    relativePath: 'src/auth.py',
    repoId: 'client',
    view: 'file',
    ...overrides,
  };
}


describe('FileTabStrip', () => {
  test('renders nothing when there are no open tabs', () => {
    const { container } = render(
      <FileTabStrip tabs={[]} activeKey={null} onSelect={() => {}} onClose={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders one tab per open file, showing the basename', () => {
    render(
      <FileTabStrip
        tabs={[tab(), tab({ key: 'client::src/other.py', relativePath: 'src/other.py' })]}
        activeKey="client::src/auth.py"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('auth.py')).toBeInTheDocument();
    expect(screen.getByText('other.py')).toBeInTheDocument();
  });

  test('the active tab gets the "active" class, the other does not', () => {
    const { container } = render(
      <FileTabStrip
        tabs={[tab(), tab({ key: 'client::src/other.py', relativePath: 'src/other.py' })]}
        activeKey="client::src/auth.py"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('.file-tab');
    expect(tabs[0].className).toContain('active');
    expect(tabs[1].className).not.toContain('active');
  });

  test('clicking a tab calls onSelect with its key', () => {
    const onSelect = vi.fn();
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={onSelect} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByText('auth.py'));
    expect(onSelect).toHaveBeenCalledWith('client::src/auth.py');
  });

  test('clicking the close button calls onClose, not onSelect', () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={onSelect} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close auth.py' }));
    expect(onClose).toHaveBeenCalledWith('client::src/auth.py');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('middle-click on the tab closes it', () => {
    const onClose = vi.fn();
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={() => {}} onClose={onClose} />,
    );
    fireEvent(
      screen.getByText('auth.py'),
      new MouseEvent('auxclick', { button: 1, bubbles: true, cancelable: true }),
    );
    expect(onClose).toHaveBeenCalledWith('client::src/auth.py');
  });

  test('right-click (auxclick button 2) does NOT close the tab', () => {
    const onClose = vi.fn();
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={() => {}} onClose={onClose} />,
    );
    fireEvent(
      screen.getByText('auth.py'),
      new MouseEvent('auxclick', { button: 2, bubbles: true, cancelable: true }),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  test('Ctrl+W on a focused tab closes it', () => {
    const onClose = vi.fn();
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={() => {}} onClose={onClose} />,
    );
    fireEvent.keyDown(screen.getByText('auth.py'), { key: 'w', ctrlKey: true });
    expect(onClose).toHaveBeenCalledWith('client::src/auth.py');
  });

  test('the title attribute shows the full repo/path, not just the basename', () => {
    render(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByTitle('client/src/auth.py')).toBeInTheDocument();
  });

  test('a vertical mouse wheel scrolls the strip horizontally (useHorizontalWheelScroll wired up)', () => {
    const { container } = render(
      <FileTabStrip
        tabs={[tab(), tab({ key: 'client::src/other.py', relativePath: 'src/other.py' })]}
        activeKey={null}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const strip = container.querySelector('.file-tab-strip');
    Object.defineProperty(strip, 'scrollLeft', { value: 0, writable: true });
    Object.defineProperty(strip, 'clientWidth', { value: 100 });
    Object.defineProperty(strip, 'scrollWidth', { value: 500 });
    const event = new WheelEvent('wheel', {
      deltaY: 50, deltaX: 0, bubbles: true, cancelable: true,
    });
    strip.dispatchEvent(event);
    expect(strip.scrollLeft).toBe(50);
    expect(event.defaultPrevented).toBe(true);
  });

  test('a diff-view tab and a file-view tab render distinct icons', () => {
    const { container } = render(
      <FileTabStrip
        tabs={[tab(), tab({ key: 'client::src/other.py', relativePath: 'src/other.py', view: 'diff' })]}
        activeKey={null}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('.file-tab');
    // File view → plain file icon; diff view → the change-kind glyph the
    // file tree uses (this fixture has no kind, so it falls back to edit).
    expect(tabs[0].querySelector('svg[data-icon]').getAttribute('data-icon')).toBe('file');
    expect(tabs[1].querySelector('.diff-file-row-kind')).toBeInTheDocument();
  });

  test('THE REGRESSION: wheel scroll still works after the FIRST tab opens (component starts with zero tabs)', () => {
    // The real-world sequence every time: FileTabStrip renders `null`
    // (no tabs open yet) BEFORE the operator opens their first file —
    // it never starts already populated. A plain useRef + useEffect
    // hook only runs its attach-effect once, on that first (empty,
    // no DOM node) render, and never re-runs once the strip actually
    // mounts — so the wheel listener silently never attached. This is
    // the exact transition that must be exercised, not just "render
    // with tabs already present" (which happened to mask the bug).
    const { container, rerender } = render(
      <FileTabStrip tabs={[]} activeKey={null} onSelect={() => {}} onClose={() => {}} />,
    );
    expect(container.querySelector('.file-tab-strip')).toBeNull();

    rerender(
      <FileTabStrip tabs={[tab()]} activeKey={null} onSelect={() => {}} onClose={() => {}} />,
    );
    const strip = container.querySelector('.file-tab-strip');
    Object.defineProperty(strip, 'scrollLeft', { value: 0, writable: true });
    Object.defineProperty(strip, 'clientWidth', { value: 100 });
    Object.defineProperty(strip, 'scrollWidth', { value: 500 });
    const event = new WheelEvent('wheel', {
      deltaY: 50, deltaX: 0, bubbles: true, cancelable: true,
    });
    strip.dispatchEvent(event);
    expect(strip.scrollLeft).toBe(50);
    expect(event.defaultPrevented).toBe(true);
  });
});

describe('FileTabStrip — diff/file view toggle', () => {

  // The diff pane used to carry its own "View file (no diff)" button in a
  // header that also repeated this tab's filename. The switch moved onto
  // the tab — first onto its leading icon, which turned out to be
  // undiscoverable (a file-type glyph reads as decoration, not a control),
  // and now onto its own button in the trailing action group beside the
  // close X. The leading icon is back to pure status.
  function renderStrip(props = {}) {
    return render(
      <FileTabStrip
        tabs={[tab(props.tab || {})]}
        activeKey={null}
        onSelect={props.onSelect || (() => {})}
        onClose={() => {}}
        onToggleView={props.onToggleView}
      />,
    );
  }

  test('the toggle switches a diff tab to the whole-file view', () => {
    const onToggleView = vi.fn();
    renderStrip({ tab: { view: 'diff' }, onToggleView });

    fireEvent.click(screen.getByRole('button', { name: /whole auth\.py file/i }));
    expect(onToggleView).toHaveBeenCalledWith('client::src/auth.py');
  });

  test('and switches a file tab back to the diff', () => {
    const onToggleView = vi.fn();
    renderStrip({ tab: { view: 'file' }, onToggleView });

    fireEvent.click(screen.getByRole('button', { name: /view the diff/i }));
    expect(onToggleView).toHaveBeenCalledWith('client::src/auth.py');
  });

  test('toggling does not also select the tab underneath', () => {
    const onSelect = vi.fn();
    renderStrip({ tab: { view: 'diff' }, onToggleView: vi.fn(), onSelect });

    fireEvent.click(screen.getByRole('button', { name: /whole auth\.py file/i }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('the toggle sits in the trailing action group, beside the close X', () => {
    // Discoverability is the whole point of the move: a tab's controls
    // live on its right edge, so that is where operators look for one.
    const { container } = renderStrip({
      tab: { view: 'file' }, onToggleView: vi.fn(),
    });
    // The leading status glyph is a bare <svg> (Icon), so read the class
    // attribute rather than .className (an SVGAnimatedString on SVG nodes).
    const controls = [...container.querySelectorAll('.file-tab > *')]
      .map((el) => `${el.tagName.toLowerCase()}.${el.getAttribute('class') || ''}`);

    expect(controls).toEqual([
      'svg.svg-inline--fa fa-file',
      'span.file-tab-label',
      'button.file-tab-view-toggle tooltip-start',
      'button.file-tab-close-btn',
    ]);
  });

  test('the leading status icon is no longer a button', () => {
    const { container } = renderStrip({
      tab: { view: 'diff', kind: 'add' }, onToggleView: vi.fn(),
    });
    const leading = container.querySelector('.file-tab > *');

    expect(leading.tagName).not.toBe('BUTTON');
  });

  test('a diff tab shows the file\'s change kind (the header used to)', () => {
    const { container } = renderStrip({
      tab: { view: 'diff', kind: 'add' }, onToggleView: vi.fn(),
    });

    expect(container.querySelector('.diff-file-row-kind.kind-add')).toBeInTheDocument();
  });

  test('stays a plain icon when no toggle handler is wired', () => {
    const { container } = renderStrip({ tab: { view: 'diff' } });

    expect(container.querySelector('.file-tab-view-toggle')).toBeNull();
    expect(container.querySelector('.file-tab svg')).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Reveal-on-tree-click + right-click menu
// --------------------------------------------------------------------------

describe('FileTabStrip — reveal and context menu', () => {
  const tabs = [
    { key: 'a', relativePath: 'src/app.py', view: 'file' },
    { key: 'b', relativePath: 'src/other.py', view: 'file' },
  ];

  function strip(props = {}) {
    return render(
      <FileTabStrip
        tabs={tabs}
        activeKey="a"
        onSelect={() => {}}
        onClose={() => {}}
        {...props}
      />,
    );
  }

  test('scrolls the active tab into view only when the reveal id changes', () => {
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const { rerender } = strip({ revealRequestId: 1 });
    scrollIntoView.mockClear();

    // A re-render that is NOT a new tree click must not move the strip —
    // this is the "don't fight my scrolling" requirement.
    rerender(
      <FileTabStrip tabs={tabs} activeKey="b" onSelect={() => {}} onClose={() => {}}
        revealRequestId={1} />,
    );
    expect(scrollIntoView).not.toHaveBeenCalled();

    rerender(
      <FileTabStrip tabs={tabs} activeKey="b" onSelect={() => {}} onClose={() => {}}
        revealRequestId={2} />,
    );
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  test('right-click opens the menu with every action', () => {
    strip({ onCloseAll: vi.fn(), onCloseOthers: vi.fn(), onTogglePin: vi.fn() });
    fireEvent.contextMenu(screen.getByText('app.py'));
    const menu = screen.getByRole('menu');
    for (const label of ['Close', 'Close others', 'Close all', 'Pin tab', 'Copy file name']) {
      expect(within(menu).getByText(label)).toBeInTheDocument();
    }
  });

  test('close others is disabled when it is the only tab', () => {
    render(
      <FileTabStrip tabs={[tabs[0]]} activeKey="a" onSelect={() => {}} onClose={() => {}}
        onCloseOthers={vi.fn()} />,
    );
    fireEvent.contextMenu(screen.getByText('app.py'));
    expect(screen.getByText('Close others')).toBeDisabled();
  });

  test('each action fires for the tab that was right-clicked, not the active one', () => {
    const onClose = vi.fn();
    const onCloseOthers = vi.fn();
    const onTogglePin = vi.fn();
    strip({ onClose, onCloseOthers, onTogglePin, onCloseAll: vi.fn() });
    fireEvent.contextMenu(screen.getByText('other.py'));   // the INACTIVE tab
    fireEvent.click(screen.getByText('Close others'));
    expect(onCloseOthers).toHaveBeenCalledWith('b');
  });

  test('a pinned tab offers Unpin', () => {
    render(
      <FileTabStrip
        tabs={[{ ...tabs[0], pinned: true }]}
        activeKey="a" onSelect={() => {}} onClose={() => {}} onTogglePin={vi.fn()}
      />,
    );
    fireEvent.contextMenu(screen.getByText('app.py'));
    expect(screen.getByText('Unpin tab')).toBeInTheDocument();
  });

  test('the menu dismisses on outside click', () => {
    strip({ onCloseAll: vi.fn() });
    fireEvent.contextMenu(screen.getByText('app.py'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.click(window);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});

describe('FileTabStrip — drag to reorder', () => {
  const t = (key, pinned = false) => ({
    key, pinned, repoId: 'client', relativePath: key, absolutePath: `/w/${key}`,
  });

  function renderTabs(tabs, onReorder = vi.fn()) {
    render(
      <FileTabStrip
        tabs={tabs}
        activeKey={tabs[0].key}
        onSelect={() => {}}
        onClose={() => {}}
        onReorder={onReorder}
      />,
    );
    return onReorder;
  }

  const pill = (name) => screen.getByTitle(`client/${name}`);

  function dragOnto(fromName, toName) {
    const data = new Map();
    const dataTransfer = {
      effectAllowed: '', dropEffect: '',
      setData: (k, v) => data.set(k, v),
      getData: (k) => data.get(k) || '',
    };
    fireEvent.dragStart(pill(fromName), { dataTransfer });
    fireEvent.dragOver(pill(toName), { dataTransfer });
    fireEvent.drop(pill(toName), { dataTransfer });
  }

  test('every tab is draggable', () => {
    renderTabs([t('a'), t('b')]);
    expect(pill('a')).toHaveAttribute('draggable', 'true');
  });

  test('dropping one tab on another reports the reorder', () => {
    const onReorder = renderTabs([t('a'), t('b'), t('c')]);
    dragOnto('a', 'c');
    expect(onReorder).toHaveBeenCalledWith('a', 'c');
  });

  test('an unpinned tab cannot be dropped into the pinned block', () => {
    // Refusing beats silently relocating: a tab that lands somewhere the
    // operator did not drop it reads as a broken drag.
    const onReorder = renderTabs([t('p', true), t('a'), t('b')]);
    dragOnto('a', 'p');
    expect(onReorder).not.toHaveBeenCalled();
  });

  test('a pinned tab cannot be dropped among the unpinned tabs', () => {
    const onReorder = renderTabs([t('p', true), t('a'), t('b')]);
    dragOnto('p', 'b');
    expect(onReorder).not.toHaveBeenCalled();
  });

  test('reordering WITHIN the pinned block is allowed', () => {
    const onReorder = renderTabs([t('p', true), t('q', true), t('a')]);
    dragOnto('q', 'p');
    expect(onReorder).toHaveBeenCalledWith('q', 'p');
  });

  test('the dragged tab is marked so the strip shows what is in flight', () => {
    renderTabs([t('a'), t('b')]);
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData: () => {}, getData: () => '' };
    fireEvent.dragStart(pill('a'), { dataTransfer });
    expect(pill('a').className).toContain('dragging');
  });

  test('a completed drop clears the drag highlight', () => {
    renderTabs([t('a'), t('b')]);
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData: () => {}, getData: () => '' };
    fireEvent.dragStart(pill('a'), { dataTransfer });
    fireEvent.dragOver(pill('b'), { dataTransfer });
    expect(pill('b').className).toContain('drop-target');
    fireEvent.drop(pill('b'), { dataTransfer });
    expect(pill('b').className).not.toContain('drop-target');
    expect(pill('a').className).not.toContain('dragging');
  });

  test('an abandoned drag (dragEnd, no drop) clears the highlight too', () => {
    renderTabs([t('a'), t('b')]);
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData: () => {}, getData: () => '' };
    fireEvent.dragStart(pill('a'), { dataTransfer });
    fireEvent.dragEnd(pill('a'), { dataTransfer });
    expect(pill('a').className).not.toContain('dragging');
  });
});


// The markdown preview ⇄ source switch. It appears only where there is a
// rendered view to switch to: a markdown file, shown as a file (a diff is
// already a source view — rendering it would hide the +/- lines).
describe('FileTabStrip — markdown preview toggle', () => {
  function md(overrides = {}) {
    return tab({
      key: '/wks/T1/plan.md',
      absolutePath: '/wks/T1/plan.md',
      relativePath: 'plan.md',
      repoId: 'task files',
      ...overrides,
    });
  }
  const noop = () => {};

  function renderStrip(t, onToggleMarkdownView = noop) {
    return render(
      <FileTabStrip
        tabs={[t]} activeKey={t.key}
        onSelect={noop} onClose={noop} onToggleView={noop}
        onToggleMarkdownView={onToggleMarkdownView}
      />,
    );
  }

  test('offers the source switch on a previewed task document', () => {
    renderStrip(md());
    expect(screen.getByLabelText('View the plan.md markdown source')).toBeTruthy();
  });

  test('offers the preview switch on a repo markdown file', () => {
    renderStrip(md({ repoId: 'client', relativePath: 'README.md',
      absolutePath: '/wks/T1/client/README.md' }));
    expect(
      screen.getByLabelText('View the rendered preview of README.md'),
    ).toBeTruthy();
  });

  test('is absent on a non-markdown file', () => {
    renderStrip(tab());
    expect(screen.queryByLabelText(/markdown source|rendered preview/)).toBeNull();
  });

  test('is absent while the tab shows a diff', () => {
    renderStrip(md({ view: 'diff' }));
    expect(screen.queryByLabelText(/markdown source|rendered preview/)).toBeNull();
  });

  test('clicking it toggles the tab, without selecting the tab', () => {
    const onToggleMarkdownView = vi.fn();
    const onSelect = vi.fn();
    render(
      <FileTabStrip
        tabs={[md()]} activeKey={md().key}
        onSelect={onSelect} onClose={noop} onToggleView={noop}
        onToggleMarkdownView={onToggleMarkdownView}
      />,
    );
    fireEvent.click(screen.getByLabelText('View the plan.md markdown source'));
    expect(onToggleMarkdownView).toHaveBeenCalledWith('/wks/T1/plan.md');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('is absent when no handler is wired', () => {
    render(
      <FileTabStrip
        tabs={[md()]} activeKey={md().key}
        onSelect={noop} onClose={noop} onToggleView={noop}
      />,
    );
    expect(screen.queryByLabelText(/markdown source|rendered preview/)).toBeNull();
  });
});
