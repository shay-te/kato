// Layout's chat-maximize class — the half of the toggle that actually
// collapses the panes.
//
// The button lives in the chat header, several levels down and inside a
// component App remounts on every task switch; this grid is its sibling. They
// meet at the shared preference, so this file pins that Layout follows it.

import { describe, test, expect, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';

import Layout from './Layout.jsx';
import {
  writeChatMaximized,
  _resetChatMaximizedPref,
} from '../utils/chatMaximizedPref.js';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetChatMaximizedPref();
});
afterEach(() => { _resetChatMaximizedPref(); });

const panes = {
  top: <div id="tabs" />,
  left: <div id="left" />,
  center: <div id="center" />,
  right: <div id="right" />,
};

function layoutEl(container) {
  return container.querySelector('#layout');
}

describe('Layout — chat maximize', () => {
  test('normal layout carries no maximize class', () => {
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
  });

  test('reads the stored preference on mount', () => {
    writeChatMaximized(true);
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).toContain('is-chat-maximized');
  });

  test('follows the preference live, with no prop and no remount', () => {
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');

    act(() => { writeChatMaximized(true); });
    expect(layoutEl(container).className).toContain('is-chat-maximized');

    act(() => { writeChatMaximized(false); });
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
  });

  test('every pane stays MOUNTED while maximized', () => {
    // The columns collapse to zero width rather than unmounting. Monaco keeps
    // scroll position, folds and the find widget in the live editor instance,
    // so tearing it down and rebuilding it a moment later is exactly the
    // "UI shifts while you are reading it" behaviour the diff viewer already
    // has rules against.
    writeChatMaximized(true);
    const { container } = render(<Layout {...panes} />);
    expect(container.querySelector('#left')).toBeInTheDocument();
    expect(container.querySelector('#center')).toBeInTheDocument();
    expect(container.querySelector('#right')).toBeInTheDocument();
  });

  test('the legacy sidebar shell is unaffected', () => {
    // No ``top`` means the old shell, which has no chat column to maximize.
    writeChatMaximized(true);
    const { container } = render(
      <Layout left={panes.left} center={panes.center} right={panes.right} />,
    );
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
    expect(layoutEl(container).className).not.toContain('has-top-tabs');
  });

  test('pane widths still reach the grid while maximized', () => {
    // The CSS zeroes the columns; the vars stay set so restoring returns the
    // operator to the widths they had, not to the defaults.
    writeChatMaximized(true);
    const { container } = render(
      <Layout {...panes} leftWidth={321} rightWidth={654} />,
    );
    const style = layoutEl(container).getAttribute('style');
    expect(style).toContain('321px');
    expect(style).toContain('654px');
  });
});
