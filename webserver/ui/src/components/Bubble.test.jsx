// Tests for Bubble. The component renders a chat bubble with a
// kind-driven CSS class, a kind-driven label, and children verbatim.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import Bubble from './Bubble.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';


describe('Bubble', () => {

  test('renders children verbatim', () => {
    render(<Bubble kind={BUBBLE_KIND.USER}>hello world</Bubble>);
    expect(screen.getByText('hello world')).toBeInTheDocument();
  });

  test('kind prop drives the CSS class — bubble plus the raw kind value', () => {
    const { container } = render(
      <Bubble kind={BUBBLE_KIND.USER}>x</Bubble>,
    );
    const bubble = container.querySelector('.bubble');
    expect(bubble).toBeInTheDocument();
    // className is "bubble user" for kind=user.
    expect(bubble.className).toBe('bubble user');
  });

  test('known kinds map to their human label', () => {
    render(<Bubble kind={BUBBLE_KIND.ASSISTANT}>reply</Bubble>);
    expect(screen.getByText('Claude')).toBeInTheDocument();

    render(<Bubble kind={BUBBLE_KIND.SYSTEM}>info</Bubble>);
    expect(screen.getByText('System')).toBeInTheDocument();

    render(<Bubble kind={BUBBLE_KIND.ERROR}>boom</Bubble>);
    expect(screen.getByText('Error')).toBeInTheDocument();
  });

  test('unknown kind falls back to using the raw kind string as the label', () => {
    render(<Bubble kind="mystery">payload</Bubble>);
    // No mapping entry, so the label is the raw kind value.
    expect(screen.getByText('mystery')).toBeInTheDocument();
    expect(screen.getByText('payload')).toBeInTheDocument();
  });

  test('renders without crashing when kind/children are missing', () => {
    // Defensive: missing kind → label slot is empty string (undefined kind),
    // missing children → content slot empty. Shouldn't throw.
    const { container } = render(<Bubble />);
    expect(container.querySelector('.bubble')).toBeInTheDocument();
    expect(container.querySelector('.bubble-content')).toBeInTheDocument();
  });
});
