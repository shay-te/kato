// Tests for Icon. Maps a string name to a FontAwesome icon and
// renders it through FontAwesomeIcon. Unknown names render null
// (so callers can't accidentally show garbage glyphs).

import { describe, test, expect } from 'vitest';
import { render } from '@testing-library/react';

import Icon, { BusyIcon } from './Icon.jsx';


describe('Icon', () => {

  test('renders an SVG for a known icon name (bell)', () => {
    const { container } = render(<Icon name="bell" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    // The component publishes the icon's own name as data-icon.
    expect(svg.getAttribute('data-icon')).toBe('bell');
  });

  test('renders an SVG for refresh', () => {
    const { container } = render(<Icon name="refresh" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg.getAttribute('data-icon')).toBe('refresh');
  });

  test('xmark name renders the xmark icon', () => {
    const { container } = render(<Icon name="xmark" />);
    expect(container.querySelector('svg').getAttribute('data-icon')).toBe('xmark');
  });

  test('external-link renders under its own name', () => {
    const { container } = render(<Icon name="external-link" />);
    expect(container.querySelector('svg').getAttribute('data-icon'))
      .toBe('external-link');
  });

  test('diff renders under its own name', () => {
    const { container } = render(<Icon name="diff" />);
    expect(container.querySelector('svg').getAttribute('data-icon'))
      .toBe('diff');
  });

  test('unknown name renders null (no garbage glyph)', () => {
    const { container } = render(<Icon name="does-not-exist" />);
    expect(container.querySelector('svg')).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  test('passes className through, and spin as a class', () => {
    const { container } = render(<Icon name="spinner" className="my-spin" spin={true} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    // FontAwesome merges classes — our custom one is one of them.
    expect(svg.getAttribute('class')).toContain('my-spin');
    // Spin animation class is added by FontAwesome when spin={true}.
    expect(svg.getAttribute('class')).toContain('kato-icon-spin');
  });

  test('missing name (undefined) renders null safely', () => {
    const { container } = render(<Icon />);
    expect(container.firstChild).toBeNull();
  });
});


describe('BusyIcon', () => {

  test('busy=true renders the thick rotating progress ring (not a glyph)', () => {
    const { container } = render(<BusyIcon busy idle="check" />);
    expect(container.querySelector('.kato-btn-spinner')).toBeInTheDocument();
    // While busy it's the CSS ring, not a FontAwesome svg.
    expect(container.querySelector('svg')).toBeNull();
  });

  test('busy=true forwards className onto the spinner ring', () => {
    const { container } = render(<BusyIcon busy idle="check" className="my-cls" />);
    expect(container.querySelector('.kato-btn-spinner').getAttribute('class'))
      .toContain('my-cls');
  });

  test('busy=false renders the idle glyph, not spinning', () => {
    const { container } = render(<BusyIcon busy={false} idle="check" />);
    const svg = container.querySelector('svg');
    expect(svg.getAttribute('data-icon')).toBe('check');
    expect(svg.getAttribute('class')).not.toContain('fa-spin');
  });

  test('forwards extra props (className) to the underlying Icon', () => {
    const { container } = render(
      <BusyIcon busy={false} idle="arrow-up" className="my-cls" />,
    );
    expect(container.querySelector('svg').getAttribute('class'))
      .toContain('my-cls');
  });
});
