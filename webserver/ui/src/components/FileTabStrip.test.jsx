// Tests for FileTabStrip — the VS Code-style row of open-file tabs
// above the centre editor/diff pane. Renders nothing with no open
// tabs; otherwise one pill per tab (file/diff icon, basename label,
// close button); click selects, close button closes, middle-click
// closes, Ctrl/Cmd+W closes the focused tab.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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
    const icons = container.querySelectorAll('.file-tab > svg[data-icon]');
    expect(icons[0].getAttribute('data-icon')).toBe('file');
    expect(icons[1].getAttribute('data-icon')).toBe('code-compare');
  });
});
