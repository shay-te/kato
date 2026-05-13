// Tests for Layout. Renders three slots (left/center/right) in a
// single #layout div with a CSS custom property for the right-pane
// width. Trivial component — a couple of tests are enough.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import Layout from './Layout.jsx';


describe('Layout', () => {

  test('renders the three slots in order', () => {
    const { container } = render(
      <Layout
        left={<div data-testid="left">L</div>}
        center={<div data-testid="center">C</div>}
        right={<div data-testid="right">R</div>}
        rightWidth={300}
      />,
    );
    expect(screen.getByTestId('left')).toBeInTheDocument();
    expect(screen.getByTestId('center')).toBeInTheDocument();
    expect(screen.getByTestId('right')).toBeInTheDocument();
    // Order is left → center → right within the outer #layout div.
    const layout = container.querySelector('#layout');
    expect(layout.children[0]).toHaveAttribute('data-testid', 'left');
    expect(layout.children[1]).toHaveAttribute('data-testid', 'center');
    expect(layout.children[2]).toHaveAttribute('data-testid', 'right');
  });

  test('rightWidth drives the --right-pane-width CSS custom property', () => {
    const { container } = render(
      <Layout left={null} center={null} right={null} rightWidth={420} />,
    );
    const layout = container.querySelector('#layout');
    expect(layout.style.getPropertyValue('--right-pane-width')).toBe('420px');
  });

  test('renders without crashing when slots are null', () => {
    const { container } = render(
      <Layout left={null} center={null} right={null} rightWidth={0} />,
    );
    expect(container.querySelector('#layout')).toBeInTheDocument();
  });
});
