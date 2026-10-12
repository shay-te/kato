// The header's pull-request list: each repository with its open pull request,
// to open in a new tab or copy — or "none yet" / "could not check".
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../utils/clipboard.js', () => ({ copyTextToClipboard: vi.fn().mockResolvedValue(undefined) }));

import { copyTextToClipboard } from '../utils/clipboard.js';
import PullRequestList from './PullRequestList.jsx';

const API = { id: '17', title: 'T1 add login', url: 'https://github.com/acme/api/pull/17' };
const WEB = { id: '4', title: '', url: 'https://bitbucket.org/acme/web/pull-requests/4' };
const repo = (repositoryId, pullRequest = null, lookupFailed = false) => ({ repositoryId, pullRequest, lookupFailed });

function renderList(repositories, props = {}) {
  const onOpen = vi.fn();
  render(<PullRequestList repositories={repositories} onOpen={onOpen} {...props} />);
  return { onOpen, toggle: screen.getByRole('button', { name: /^Pull requests:/ }) };
}

function openList(repositories, props) {
  const rendered = renderList(repositories, props);
  fireEvent.click(rendered.toggle);
  return { ...rendered, list: screen.getByRole('dialog', { name: 'Pull requests' }) };
}

beforeEach(() => { copyTextToClipboard.mockClear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('PullRequestList — the button', () => {
  test('counts the open pull requests on its corner, coloured by how many are left', () => {
    renderList([repo('api', API), repo('web')]);
    const badge = document.querySelector('.pull-request-list-badge');
    expect(badge.textContent).toBe('1/2');
    expect(badge).toHaveClass('is-warn');
  });

  test('every repository with its pull request reads as done', () => {
    renderList([repo('api', API), repo('web', WEB)]);
    expect(document.querySelector('.pull-request-list-badge')).toHaveClass('is-good');
  });

  test('no pull request yet: no badge, but the list still opens', () => {
    const { toggle } = renderList([repo('api')]);
    expect(document.querySelector('.pull-request-list-badge')).toBeNull();
    expect(toggle).not.toBeDisabled();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  test('a click opens the list and looks the pull requests up again; a second closes it', () => {
    const { toggle, onOpen } = renderList([repo('api', API)]);
    fireEvent.click(toggle);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(toggle);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onOpen).toHaveBeenCalledTimes(1);  // only on opening
  });
});

describe('PullRequestList — while it does not know', () => {
  test('still loading: says so, not "no repositories"', () => {
    const { toggle } = renderList([], { status: 'loading' });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute('data-tooltip', 'Checking the pull requests…');
  });

  test('the lookup failed: says why, and a click tries again', () => {
    const { toggle, onOpen } = renderList([], { status: 'error', fetchError: 'HTTP 500' });
    expect(toggle).not.toBeDisabled();
    expect(toggle).toHaveAttribute('data-tooltip',
      'Could not check the pull requests (HTTP 500). Click to try again.');
    fireEvent.click(toggle);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Could not check the pull requests (HTTP 500).')).toHaveClass('is-warn');
  });

  test('from the open list it tries again, and says so while it does', async () => {
    let settle;
    const onOpen = vi.fn(() => new Promise((resolve) => { settle = resolve; }));
    render(<PullRequestList repositories={[]} status="error" fetchError="no answer within 8s" onOpen={onOpen} />);
    fireEvent.click(screen.getByRole('button', { name: /^Pull requests:/ }));
    settle();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Checking again…' })).toBeDisabled();
    settle();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).not.toBeDisabled());
    // Still open: trying again is not a click outside.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('a server that does not list them says to restart kato', () => {
    const { toggle } = renderList(null);
    expect(toggle).toBeDisabled();
    expect(toggle.getAttribute('data-tooltip')).toMatch(/older than this page.*Restart kato/);
  });
});

describe('PullRequestList — the list', () => {
  test('each repository: its pull request, none yet, or could not check', () => {
    const { list } = openList([repo('api', API), repo('web'), repo('lib', null, true)]);
    expect(within(list).getByText('Pull requests · 1 of 3 open')).toBeInTheDocument();
    const rows = [...list.querySelectorAll('.pull-request-list-row')].map((row) => row.textContent);
    expect(rows).toEqual([
      'api#17 T1 add login',
      'webNo pull request yet',
      'libCould not check — the git host did not answer',
    ]);
  });

  test('a pull request opens in a new tab', () => {
    const { list } = openList([repo('api', API)]);
    const link = within(list).getByRole('link', { name: 'Open the api pull request in a new tab' });
    expect(link).toHaveAttribute('href', API.url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  test('its link copies, and the button says so', async () => {
    const { list } = openList([repo('api', API)]);
    fireEvent.click(within(list).getByRole('button', { name: 'Copy the api pull request link' }));
    expect(copyTextToClipboard).toHaveBeenCalledWith(API.url);
    await waitFor(() => expect(within(list).getByRole('button', { name: 'Link copied' })).toBeInTheDocument());
    // Copying is not a click outside: the list stays open.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('a pull request with no link is named, with nothing to open or copy', () => {
    const { list } = openList([repo('api', { id: '9', title: '', url: '' })]);
    expect(within(list).getByText('#9')).toBeInTheDocument();
    expect(within(list).queryByRole('link')).toBeNull();
  });

  test('with more than one, all of them open in new tabs at once', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const { list } = openList([repo('api', API), repo('web', WEB), repo('lib')]);
    fireEvent.click(within(list).getByRole('button', { name: 'Open all 2 in new tabs' }));
    expect(open.mock.calls).toEqual([
      [API.url, '_blank', 'noopener,noreferrer'],
      [WEB.url, '_blank', 'noopener,noreferrer'],
    ]);
  });

  test('one pull request needs no "open all"', () => {
    const { list } = openList([repo('api', API)]);
    expect(within(list).queryByText(/Open all/)).toBeNull();
  });

  test('a credential the git host rejects is said above the list', () => {
    const { list } = openList([repo('api', null, true)], { lookupError: 'Cannot read pull requests for api' });
    expect(within(list).getByText('Cannot read pull requests for api')).toHaveClass('is-warn');
  });

  test('Escape and a click outside close it', () => {
    openList([repo('api', API)]);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Pull requests:/ }));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
