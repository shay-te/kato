// Tests for SafetyBanner. Only renders when state.bypass_permissions
// is truthy. The banner text warns the operator that every tool runs
// without per-tool prompts.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import SafetyBanner from './SafetyBanner.jsx';


describe('SafetyBanner', () => {

  test('renders nothing when state is null', () => {
    const { container } = render(<SafetyBanner state={null} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when state is undefined', () => {
    const { container } = render(<SafetyBanner />);
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when bypass_permissions is false', () => {
    const { container } = render(<SafetyBanner state={{ bypass_permissions: false }} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders the banner when bypass_permissions is true', () => {
    render(<SafetyBanner state={{ bypass_permissions: true }} />);
    expect(screen.getByText(/KATO_CLAUDE_BYPASS_PERMISSIONS=true/)).toBeInTheDocument();
    expect(screen.getByText(/running every tool without asking/)).toBeInTheDocument();
  });

  test('uses role=alert so screen readers announce it', () => {
    const { container } = render(<SafetyBanner state={{ bypass_permissions: true }} />);
    const banner = container.querySelector('[role="alert"]');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveClass('kato-safety-banner');
  });
});
