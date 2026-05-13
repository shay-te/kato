// Tests for ChangesTab. The component itself is large (462 lines)
// and composes diff fetching + per-repo accordions + per-file diff
// rendering. We focus on:
//   - the pure helpers exported from ChangesTab.jsx
//   - a minimal render path (no data → empty state)
//   - status-label mapping
//
// Heavier integration (auto-poll, per-file diff rendering) is
// already exercised by `DiffFileWithComments.test.jsx` and
// `diffFileSize.test.js`.

import { describe, test, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../webserver/ui/src/api.js', () => ({}));
vi.mock('./api.js', () => ({
  fetchTaskDiff: vi.fn().mockResolvedValue({ ok: true, body: { diffs: [] } }),
  fetchTaskComments: vi.fn().mockResolvedValue({ ok: true, body: { comments: [] } }),
  syncRemoteComments: vi.fn(),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import ChangesTab, {
  basenameOf,
  diffFileKey,
  diffLabelForStatus,
} from './ChangesTab.jsx';


describe('diffLabelForStatus — maps server status to header chip', () => {

  test('review → "already pushed (PR open)"', () => {
    expect(diffLabelForStatus('review')).toBe('already pushed (PR open)');
  });

  test('done → "merged"', () => {
    expect(diffLabelForStatus('done')).toBe('merged');
  });

  test('errored → "publish errored"', () => {
    expect(diffLabelForStatus('errored')).toBe('publish errored');
  });

  test('terminated → "terminated"', () => {
    expect(diffLabelForStatus('terminated')).toBe('terminated');
  });

  test('unknown / empty status → "" (no chip)', () => {
    expect(diffLabelForStatus('')).toBe('');
    expect(diffLabelForStatus('active')).toBe('');
    expect(diffLabelForStatus(undefined)).toBe('');
    expect(diffLabelForStatus(null)).toBe('');
  });

  test('case-insensitive matching', () => {
    expect(diffLabelForStatus('REVIEW')).toBe('already pushed (PR open)');
    expect(diffLabelForStatus('Done')).toBe('merged');
  });
});


describe('basenameOf — derives the last path segment', () => {

  test('forward-slash path', () => {
    expect(basenameOf('/workspaces/PROJ-1/client')).toBe('client');
  });

  test('backslash path (Windows-style)', () => {
    expect(basenameOf('C:\\workspaces\\PROJ-1\\client')).toBe('client');
  });

  test('trailing slash stripped before extraction', () => {
    expect(basenameOf('/workspaces/PROJ-1/client/')).toBe('client');
  });

  test('empty / null returns empty string', () => {
    expect(basenameOf('')).toBe('');
    expect(basenameOf(null)).toBe('');
    expect(basenameOf(undefined)).toBe('');
  });

  test('single-segment path returns itself', () => {
    expect(basenameOf('client')).toBe('client');
  });
});


describe('diffFileKey — stable identity for react-diff-view keying', () => {

  test('uses type + oldPath + newPath', () => {
    const key = diffFileKey({
      type: 'modify', oldPath: 'src/a.py', newPath: 'src/a.py',
    });
    expect(key).toContain('modify');
    expect(key).toContain('src/a.py');
  });

  test('rename: old and new paths differ', () => {
    const key = diffFileKey({
      type: 'rename', oldPath: 'src/old.py', newPath: 'src/new.py',
    });
    expect(key).toContain('src/old.py');
    expect(key).toContain('src/new.py');
  });

  test('add: only newPath relevant', () => {
    const key = diffFileKey({
      type: 'add', oldPath: '', newPath: 'src/new.py',
    });
    expect(key).toContain('add');
    expect(key).toContain('src/new.py');
  });

  test('delete: only oldPath relevant', () => {
    const key = diffFileKey({
      type: 'delete', oldPath: 'src/old.py', newPath: '',
    });
    expect(key).toContain('delete');
    expect(key).toContain('src/old.py');
  });

  test('two different files produce different keys', () => {
    const a = diffFileKey({
      type: 'modify', oldPath: 'src/a.py', newPath: 'src/a.py',
    });
    const b = diffFileKey({
      type: 'modify', oldPath: 'src/b.py', newPath: 'src/b.py',
    });
    expect(a).not.toBe(b);
  });
});


describe('ChangesTab — render shell', () => {

  test('renders without crashing when activeTaskId is null', () => {
    const { container } = render(
      <ChangesTab activeTaskId={null} onAddToChat={vi.fn()} />,
    );
    expect(container).toBeInTheDocument();
  });
});
