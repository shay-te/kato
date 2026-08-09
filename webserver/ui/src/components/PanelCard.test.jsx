import { describe, expect, test, vi } from 'vitest';
import { render } from '@testing-library/react';
import PanelCard from './PanelCard.jsx';

describe('PanelCard', () => {
  test('wraps children in the shared card + content shell', () => {
    const { container } = render(<PanelCard><p>body</p></PanelCard>);

    const card = container.querySelector('.panel-card');
    const content = card.querySelector('.panel-card-content');
    expect(content.textContent).toBe('body');
  });

  test('renders as the requested element with the given ids/classes', () => {
    const { container } = render(
      <PanelCard
        as="aside"
        id="right-pane"
        className="extra"
        contentId="right-pane-root"
        contentClassName="center-pane-with-tabs"
      >
        body
      </PanelCard>,
    );

    const card = container.querySelector('aside#right-pane');
    expect(card.className).toBe('panel-card extra');
    const content = card.querySelector('#right-pane-root');
    expect(content.className).toBe('panel-card-content center-pane-with-tabs');
  });

  test('renders the resize handle as a card child, outside the clipped content', () => {
    // Load-bearing: the handle anchors into the layout gutter, so it must
    // NOT sit inside .panel-card-content (which clips to the corners).
    const onResizePointerDown = vi.fn();
    const { container } = render(
      <PanelCard resizerId="right-pane-resizer" onResizePointerDown={onResizePointerDown}>
        body
      </PanelCard>,
    );

    const handle = container.querySelector('#right-pane-resizer');
    expect(handle.parentElement.className).toBe('panel-card');
  });

  test('omits the handle when no resize callback is wired', () => {
    const { container } = render(
      <PanelCard resizerId="right-pane-resizer">body</PanelCard>,
    );

    expect(container.querySelector('#right-pane-resizer')).toBeNull();
  });

  test('forwards an inline style to the card element', () => {
    const { container } = render(<PanelCard style={{ width: 420 }}>body</PanelCard>);

    expect(container.querySelector('.panel-card').style.width).toBe('420px');
  });
});
