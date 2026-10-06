// Where an approval request is drawn — switched from the request itself.
//
// Real throughout: GlobalPermissionContainer, the shared permissionStore and
// its poller, the API helpers, the preference store, the answer form and its
// draft. The ONE stand-in is the network: ``fetch`` answers
// /api/permissions/pending (and the decision POST) from memory.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import ChatSettingsPanel from './ChatSettingsPanel.jsx';
import GlobalPermissionContainer from './GlobalPermissionContainer.jsx';
import { permissionStore } from '../stores/permissionStore.js';
import {
  APPROVAL_MODE_GLOBAL,
  APPROVAL_MODE_IN_CHAT,
  _resetApprovalModePref,
  readApprovalMode,
  writeApprovalMode,
} from '../utils/approvalModePref.js';

function ask(taskId, requestId, tool = 'Bash', input = { command: 'npm test' }) {
  return {
    task_id: taskId, task_summary: `${taskId} summary`, type: 'control_request', request_id: requestId,
    request: { request_id: requestId, tool_name: tool, input },
  };
}

const QUESTION = {
  questions: [{
    question: 'Make the reasoning set-once too?',
    header: 'Reasoning',
    multiSelect: false,
    options: [{ label: 'Yes, set-once', description: 'Locked once written.' }, { label: 'No, editable' }],
  }],
};

let pending;
beforeEach(() => {
  pending = [];
  permissionStore.__resetForTests();
  _resetApprovalModePref();
  window.localStorage.removeItem('kato.approvalMode.v1');
  vi.stubGlobal('fetch', async (url, init = {}) => {
    const body = url.includes('/api/permissions/pending')
      ? { pending }
      : { status: 'delivered' };
    return { ok: true, status: 200, statusText: '', json: async () => body };
  });
  const slot = document.createElement('div');
  slot.id = 'chat-permission-slot';
  document.body.appendChild(slot);
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.getElementById('chat-permission-slot')?.remove();
  _resetApprovalModePref();
});

function inChatCard() {
  return document.querySelector('#chat-permission-slot #permission-modal.is-inline');
}

describe('popup ⇄ chat, from the request', () => {
  test('a popup minimizes into the chat, and the choice sticks for every task', async () => {
    writeApprovalMode(APPROVAL_MODE_GLOBAL);
    pending = [ask('UNA-1', 'r1')];
    render(<GlobalPermissionContainer activeTaskId="UNA-1" />);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Minimize to the chat' }));

    expect(readApprovalMode()).toBe(APPROVAL_MODE_IN_CHAT);
    await waitFor(() => expect(inChatCard()).toBeInTheDocument());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // The SAME request moved — still titled with its task.
    expect(inChatCard()).toHaveTextContent('UNA-1');
    expect(window.localStorage.getItem('kato.approvalMode.v1')).toContain('in-chat');
  });

  test('a request in the chat expands back to a popup', async () => {
    writeApprovalMode(APPROVAL_MODE_IN_CHAT);
    pending = [ask('UNA-1', 'r2')];
    render(<GlobalPermissionContainer activeTaskId="UNA-1" />);
    await waitFor(() => expect(inChatCard()).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Expand to a popup' }));

    expect(readApprovalMode()).toBe(APPROVAL_MODE_GLOBAL);
    expect(await screen.findByRole('dialog')).toHaveTextContent('UNA-1');
    expect(inChatCard()).not.toBeInTheDocument();
  });

  test('after minimizing, another task\'s request no longer interrupts', async () => {
    writeApprovalMode(APPROVAL_MODE_GLOBAL);
    pending = [ask('UNA-1', 'r3')];
    render(<GlobalPermissionContainer activeTaskId="UNA-1" />);
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: 'Minimize to the chat' }));
    await waitFor(() => expect(inChatCard()).toBeInTheDocument());

    // Answered, then a background task asks.
    await act(async () => { permissionStore.resolve('r3'); });
    pending = [ask('UNA-2', 'r4')];
    await waitFor(() => expect(screen.getByRole('status', { name: /waiting for you/ })).toHaveTextContent('UNA-2'), { timeout: 4000 });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(inChatCard()).not.toBeInTheDocument();
  });

  test('a half-answered question keeps its answer across the switch', async () => {
    writeApprovalMode(APPROVAL_MODE_GLOBAL);
    pending = [ask('UNA-1', 'r5', 'AskUserQuestion', QUESTION)];
    render(<GlobalPermissionContainer activeTaskId="UNA-1" />);
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByLabelText(/Yes, set-once/));
    expect(screen.getByLabelText(/Yes, set-once/)).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Minimize to the chat' }));
    await waitFor(() => expect(inChatCard()).toBeInTheDocument());
    expect(screen.getByLabelText(/Yes, set-once/)).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Expand to a popup' }));
    await screen.findByRole('dialog');
    expect(screen.getByLabelText(/Yes, set-once/)).toBeChecked();
  });
});

describe('Settings no longer carries the option', () => {
  test('the chat settings panel has no approval-placement choice', () => {
    render(<ChatSettingsPanel />);
    expect(screen.queryByText('When the agent asks for approval')).not.toBeInTheDocument();
    expect(screen.queryByText('A window over everything')).not.toBeInTheDocument();
    expect(document.querySelector('input[name="approval-mode"]')).toBeNull();
  });
});
