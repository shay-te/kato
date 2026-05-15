// Tests for Icon. Maps a string name to a FontAwesome icon and
// renders it through FontAwesomeIcon. Unknown names render null
// (so callers can't accidentally show garbage glyphs).

import { describe, test, expect } from 'vitest';
import { render } from '@testing-library/react';

import Icon from './Icon.jsx';


describe('Icon', () => {

  test('renders an SVG for a known icon name (bell)', () => {
    const { container } = render(<Icon name="bell" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    // FontAwesome encodes the icon id in the data-icon attribute.
    expect(svg.getAttribute('data-icon')).toBe('bell');
  });

  test('renders an SVG for refresh (mapped to arrows-rotate)', () => {
    const { container } = render(<Icon name="refresh" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg.getAttribute('data-icon')).toBe('arrows-rotate');
  });

  test('xmark name renders the xmark icon', () => {
    const { container } = render(<Icon name="xmark" />);
    expect(container.querySelector('svg').getAttribute('data-icon')).toBe('xmark');
  });

  test('external-link maps to the open-in-new-tab glyph', () => {
    const { container } = render(<Icon name="external-link" />);
    expect(container.querySelector('svg').getAttribute('data-icon'))
      .toBe('arrow-up-right-from-square');
  });

  test('unknown name renders null (no garbage glyph)', () => {
    const { container } = render(<Icon name="does-not-exist" />);
    expect(container.querySelector('svg')).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  test('passes className and spin props through to FontAwesome', () => {
    const { container } = render(<Icon name="spinner" className="my-spin" spin={true} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    // FontAwesome merges classes — our custom one is one of them.
    expect(svg.getAttribute('class')).toContain('my-spin');
    // Spin animation class is added by FontAwesome when spin={true}.
    expect(svg.getAttribute('class')).toContain('fa-spin');
  });

  test('missing name (undefined) renders null safely', () => {
    const { container } = render(<Icon />);
    expect(container.firstChild).toBeNull();
  });
});
