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
  MAXIMIZED_PANE,
  writeMaximizedPane,
  _resetMaximizedPanePref,
} from '../utils/maximizedPanePref.js';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetMaximizedPanePref();
});
afterEach(() => { _resetMaximizedPanePref(); });

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
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).toContain('is-chat-maximized');
  });

  test('follows the preference live, with no prop and no remount', () => {
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');

    act(() => { writeMaximizedPane(MAXIMIZED_PANE.CHAT); });
    expect(layoutEl(container).className).toContain('is-chat-maximized');

    act(() => { writeMaximizedPane(MAXIMIZED_PANE.NONE); });
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
  });

  test('every pane stays MOUNTED while maximized', () => {
    // The columns collapse to zero width rather than unmounting. Monaco keeps
    // scroll position, folds and the find widget in the live editor instance,
    // so tearing it down and rebuilding it a moment later is exactly the
    // "UI shifts while you are reading it" behaviour the diff viewer already
    // has rules against.
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    const { container } = render(<Layout {...panes} />);
    expect(container.querySelector('#left')).toBeInTheDocument();
    expect(container.querySelector('#center')).toBeInTheDocument();
    expect(container.querySelector('#right')).toBeInTheDocument();
  });

  test('the legacy sidebar shell is unaffected', () => {
    // No ``top`` means the old shell, which has no chat column to maximize.
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    const { container } = render(
      <Layout left={panes.left} center={panes.center} right={panes.right} />,
    );
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
    expect(layoutEl(container).className).not.toContain('has-top-tabs');
  });

  test('pane widths still reach the grid while maximized', () => {
    // The CSS zeroes the columns; the vars stay set so restoring returns the
    // operator to the widths they had, not to the defaults.
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    const { container } = render(
      <Layout {...panes} leftWidth={321} rightWidth={654} />,
    );
    const style = layoutEl(container).getAttribute('style');
    expect(style).toContain('321px');
    expect(style).toContain('654px');
  });
});


describe('Layout — centre maximize', () => {
  test('the centre maximized collapses the tree and the chat, every pane still mounted', () => {
    writeMaximizedPane(MAXIMIZED_PANE.CENTER);
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).toContain('is-center-maximized');
    expect(layoutEl(container).className).not.toContain('is-chat-maximized');
    for (const id of ['#left', '#center', '#right']) {
      expect(container.querySelector(id)).toBeInTheDocument();
    }
  });

  test('one pane at a time: maximizing the chat un-maximizes the centre', () => {
    const { container } = render(<Layout {...panes} />);
    act(() => { writeMaximizedPane(MAXIMIZED_PANE.CENTER); });
    expect(layoutEl(container).className).toContain('is-center-maximized');
    act(() => { writeMaximizedPane(MAXIMIZED_PANE.CHAT); });
    expect(layoutEl(container).className).toContain('is-chat-maximized');
    expect(layoutEl(container).className).not.toContain('is-center-maximized');
  });

  test('the legacy sidebar shell is unaffected', () => {
    writeMaximizedPane(MAXIMIZED_PANE.CENTER);
    const { container } = render(<Layout left={panes.left} center={panes.center} right={panes.right} />);
    expect(layoutEl(container).className).not.toContain('is-center-maximized');
  });
});


describe('Layout — the files rail', () => {
  // ONE answer (``filesRailShown``): whichever pane is maximized, the files
  // tree stays as a slim rail beside it.
  test('beside a maximized chat and beside a maximized centre; not otherwise', () => {
    const { container } = render(<Layout {...panes} />);
    expect(layoutEl(container).className).not.toContain('has-files-rail');
    act(() => { writeMaximizedPane(MAXIMIZED_PANE.CHAT); });
    expect(layoutEl(container).className).toContain('has-files-rail');
    act(() => { writeMaximizedPane(MAXIMIZED_PANE.CENTER); });
    expect(layoutEl(container).className).toContain('has-files-rail');
    act(() => { writeMaximizedPane(MAXIMIZED_PANE.NONE); });
    expect(layoutEl(container).className).not.toContain('has-files-rail');
  });

  test('not in the legacy sidebar shell', () => {
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    const { container } = render(<Layout left={panes.left} center={panes.center} right={panes.right} />);
    expect(layoutEl(container).className).not.toContain('has-files-rail');
  });
});
