// Component-level tests for MessageForm. The helpers it calls
// (composerDraft.js) already have their own test suite; this file
// proves the React wiring is correct end-to-end:
//
//   - Mount with taskId → reads existing draft into the textarea.
//   - Typing → mirrors to localStorage on every keystroke.
//   - Unmount + remount with the same taskId → draft is back.
//   - Tab switch (different taskId) → tabs don't see each other's drafts.
//   - Submit clears both the visible textarea AND the persisted draft.
//
// These were previously covered ONLY at the helper level. The
// operator-reported bug ("I type then switch tabs then come back
// and my input is gone") is wiring, not helpers, so it lives here.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { createRef } from 'react';

// In-memory stand-in for IndexedDB (jsdom has none). composerImageDraft.js
// reads/writes through idbStore, so mocking idbStore lets us drive the durable
// image-attachment path without a real IndexedDB. idbGet captures the value at
// CALL time (matching a real readonly transaction, which reads the value as it
// was when the tx was created — before a later delete commits); when
// _idbGate.promise is set, resolution is delayed so a test can interleave a
// send between the read being issued and it resolving.
const { _idbMem, _idbGate } = vi.hoisted(() => ({
  _idbMem: new Map(),
  _idbGate: { promise: null },
}));
vi.mock('../utils/idbStore.js', () => ({
  idbGet: (key) => {
    const snapshot = _idbMem.get(key);
    return _idbGate.promise
      ? _idbGate.promise.then(() => snapshot)
      : Promise.resolve(snapshot);
  },
  idbSet: async (key, value) => { _idbMem.set(key, value); },
  idbDelete: async (key) => { _idbMem.delete(key); },
  _resetIdbConnection: () => {},
}));

// In-memory stand-in for the server-side draft store (/api/sessions/<id>/draft).
const { _serverDrafts } = vi.hoisted(() => ({ _serverDrafts: new Map() }));
vi.mock('../api.js', () => ({
  fetchDraft: async (taskId) => _serverDrafts.get(taskId) || { text: '', images: [] },
  saveDraft: async (taskId, draft) => {
    const hasImages = Array.isArray(draft.images) && draft.images.length > 0;
    if (!draft.text && !hasImages) { _serverDrafts.delete(taskId); }
    else { _serverDrafts.set(taskId, draft); }
  },
}));

// Mutable so a test can flip whether the installed CLI supports workflows
// (gates the ultracode toggle). Default: supported, so the toggle renders.
const { _agentVer } = vi.hoisted(() => ({ _agentVer: { value: { supports_workflows: true } } }));
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: () => _agentVer.value,
  resetAgentVersionCacheForTests: () => {},
}));

import MessageForm from './MessageForm.jsx';
import { DRAFT_STORAGE_PREFIX, ULTRACODE_STORAGE_PREFIX } from '../utils/composerDraft.js';
import { IMAGE_DRAFT_PREFIX, clearImageDraft } from '../utils/composerImageDraft.js';


function renderForm({ taskId = 'T1', onSubmit = vi.fn(), ...rest } = {}) {
  return {
    onSubmit,
    ...render(
      <MessageForm
        taskId={taskId}
        turnInFlight={false}
        onSubmit={onSubmit}
        {...rest}
      />,
    ),
  };
}


describe('MessageForm — draft persistence (operator scenario)', () => {

  test('hydrates from localStorage on mount when a draft exists for taskId', () => {
    window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}T1`, 'preserved draft');

    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveValue('preserved draft');
  });

  test('mirrors every keystroke into localStorage keyed by taskId', () => {
    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'in progress' } });

    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('in progress');
  });

  test('full A → B → A scenario: switching tabs preserves both drafts', () => {
    // Mount tab A and type.
    const { unmount } = renderForm({ taskId: 'A' });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'message-for-A' },
    });
    unmount();  // SessionDetail unmount on tab switch

    // Mount tab B and type.
    const { unmount: unmountB } = renderForm({ taskId: 'B' });
    expect(screen.getByRole('textbox')).toHaveValue('');  // B starts empty
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'message-for-B' },
    });
    unmountB();

    // Back to A — its draft must be intact.
    renderForm({ taskId: 'A' });
    expect(screen.getByRole('textbox')).toHaveValue('message-for-A');
  });

  test('submit clears both the textarea AND the persisted draft on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    const { container } = renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'send this' } });
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('send this');

    // Form submit (Enter key) — Shift not held.
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    expect(onSubmit).toHaveBeenCalledWith('send this', []);
    // Submit is now async; wait for the post-await state clear.
    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`)).toBeNull();
  });

  test('ultracode toggle prepends the keyword to the sent message', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    renderForm({ taskId: 'T1', onSubmit });
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'audit the cascade' } });
    // Off by default → plain text.
    const toggle = screen.getByRole('button', { name: /ultracode/i });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    // Arm it, then send.
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith('ultracode\n\naudit the cascade', []);
  });

  test('ultracode toggle is hidden when the CLI does not support workflows', () => {
    _agentVer.value = { supports_workflows: false };
    try {
      renderForm({ taskId: 'T1' });
      expect(screen.queryByRole('button', { name: /ultracode/i })).toBeNull();
    } finally {
      _agentVer.value = { supports_workflows: true };
    }
  });

  test('a stale ultracode toggle does not inject the keyword on an unsupported CLI', async () => {
    // localStorage says ultracode was armed, but the CLI now lacks support →
    // the keyword must NOT be prepended.
    window.localStorage.setItem(`${ULTRACODE_STORAGE_PREFIX}T1`, 'on');
    _agentVer.value = { supports_workflows: false };
    try {
      const onSubmit = vi.fn().mockResolvedValue(true);
      renderForm({ taskId: 'T1', onSubmit });
      const textarea = screen.getByRole('textbox');
      fireEvent.change(textarea, { target: { value: 'audit the cascade' } });
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
      expect(onSubmit).toHaveBeenCalledWith('audit the cascade', []);
    } finally {
      _agentVer.value = { supports_workflows: true };
      window.localStorage.removeItem(`${ULTRACODE_STORAGE_PREFIX}T1`);
    }
  });

  test('ultracode OFF sends the message unchanged', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    renderForm({ taskId: 'T1', onSubmit });
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'just a normal message' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith('just a normal message', []);
  });

  test('ultracode chip survives tab switch and is isolated per task', () => {
    // Arm ultracode on tab A.
    const { unmount } = renderForm({ taskId: 'A' });
    const toggleA = screen.getByRole('button', { name: /ultracode/i });
    expect(toggleA).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggleA);
    expect(toggleA).toHaveAttribute('aria-pressed', 'true');
    unmount();

    // Switch to tab B — must start off (per-task isolation).
    const { unmount: unmountB } = renderForm({ taskId: 'B' });
    const toggleB = screen.getByRole('button', { name: /ultracode/i });
    expect(toggleB).toHaveAttribute('aria-pressed', 'false');
    unmountB();

    // Back to tab A — must still be armed.
    renderForm({ taskId: 'A' });
    const toggleAAgain = screen.getByRole('button', { name: /ultracode/i });
    expect(toggleAAgain).toHaveAttribute('aria-pressed', 'true');
  });

  test('Bug: draft + textarea survive when onSubmit returns false (send failed)', async () => {
    // Operator clicks Send → backend returns an error envelope →
    // SessionDetail's onSendMessage returns false. The draft must
    // stay intact so the operator can retry without retyping.
    const onSubmit = vi.fn().mockResolvedValue(false);
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'might fail' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('might fail');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('might fail');
  });

  test('Bug: draft + textarea survive when onSubmit throws (network error)', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('network down'));
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'mid-flight' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('mid-flight');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('mid-flight');
  });

  test('Shift+Enter inserts a newline and does NOT submit', () => {
    const onSubmit = vi.fn();
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'line 1' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('imperative clear() wipes textarea AND localStorage', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'about to clear' },
    });

    act(() => { ref.current.clear(); });

    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`)).toBeNull();
  });

  test('imperative appendFragment merges into the existing draft', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'please review' },
    });
    act(() => { ref.current.appendFragment('src/auth.py'); });

    expect(screen.getByRole('textbox')).toHaveValue('please review src/auth.py');
    // And the merged value is persisted too.
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('please review src/auth.py');
  });

  test('imperative appendFragment keeps the appended caret visible', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );
    const textarea = screen.getByRole('textbox');
    Object.defineProperty(textarea, 'scrollHeight', {
      value: 1234,
      configurable: true,
    });
    textarea.focus = vi.fn();
    textarea.setSelectionRange = vi.fn();

    act(() => { ref.current.appendFragment('client:src/auth.py'); });

    const caret = 'client:src/auth.py'.length;
    expect(textarea.focus).toHaveBeenCalled();
    expect(textarea.setSelectionRange).toHaveBeenCalledWith(caret, caret);
    expect(textarea.scrollTop).toBe(1234);
  });

  test('empty composer starts as a single-line field', () => {
    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveAttribute('rows', '1');
    expect(textarea).toHaveAttribute('placeholder', 'Reply to Claude');
  });
});


describe('MessageForm — image attachment persistence (IndexedDB)', () => {
  beforeEach(() => { _idbMem.clear(); _idbGate.promise = null; });

  test('restores a persisted image attachment on mount (survives a reload)', async () => {
    // A prior session stored an image for T1 (as the composer does on paste).
    _idbMem.set(`${IMAGE_DRAFT_PREFIX}T1`, [{ media_type: 'image/png', data: 'AAAA' }]);

    const { container } = renderForm({ taskId: 'T1' });

    // Hydration is async (IndexedDB) — wait for the preview to render, rebuilt
    // as a data: URL from the stored part.
    await waitFor(() => {
      expect(container.querySelector('.message-attachment img')).toBeTruthy();
    });
    expect(container.querySelector('.message-attachment img'))
      .toHaveAttribute('src', 'data:image/png;base64,AAAA');
  });

  test('a different task does not see another task\'s stored images', async () => {
    _idbMem.set(`${IMAGE_DRAFT_PREFIX}T1`, [{ media_type: 'image/png', data: 'AAAA' }]);

    const { container } = renderForm({ taskId: 'T2' });

    // Let the async hydrate settle, then assert T2 has no image.
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-attachment img')).toBeNull();
  });

  test('a corrupt persisted entry is ignored, not crashed on', async () => {
    _idbMem.set(`${IMAGE_DRAFT_PREFIX}T1`, [{ media_type: '', data: '' }, null, 'bad']);

    const { container } = renderForm({ taskId: 'T1' });

    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-attachment img')).toBeNull();
    // Composer still usable.
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  test('a send during the in-flight image read does NOT resurrect the sent image', async () => {
    // Race regression: the IDB read is issued at mount (capturing the stored
    // image) but is slow to resolve. If the operator sends in that window, the
    // stale read must NOT re-apply the just-sent image (it would risk a
    // duplicate re-send). The `length === 0` guard alone can't catch this —
    // hence imagesSettledRef is flipped synchronously on send.
    window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}T1`, 'fix the bug'); // text so the send proceeds
    _idbMem.set(`${IMAGE_DRAFT_PREFIX}T1`, [{ media_type: 'image/png', data: 'AAAA' }]);
    let releaseRead;
    _idbGate.promise = new Promise((r) => { releaseRead = r; }); // hold the read open

    const onSubmit = vi.fn().mockResolvedValue(true);
    const { container } = renderForm({ taskId: 'T1', onSubmit });

    // Operator sends (text is present, hydrated synchronously) before the image
    // read resolves.
    await act(async () => {
      fireEvent.submit(container.querySelector('form'));
    });
    expect(onSubmit).toHaveBeenCalledWith('fix the bug', []);

    // Now let the stale read resolve — it must be ignored.
    await act(async () => { releaseRead(); await Promise.resolve(); });

    expect(container.querySelector('.message-attachment img')).toBeNull();
  });

  test('clearing a task\'s image draft purges its durable entry (forget cleanup)', async () => {
    _idbMem.set(`${IMAGE_DRAFT_PREFIX}T1`, [{ media_type: 'image/png', data: 'AAAA' }]);
    await clearImageDraft('T1');
    expect(_idbMem.has(`${IMAGE_DRAFT_PREFIX}T1`)).toBe(false);
  });
});


describe('MessageForm — disabled + working states', () => {

  test('disabled prop blocks submission even on Enter', () => {
    const onSubmit = vi.fn();
    renderForm({
      taskId: 'T1', onSubmit,
      disabled: true,
      disabledReason: 'No record for this task on the server.',
    });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toBeDisabled();
    // Even if somehow Enter fires, submit must be a no-op.
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('disabled placeholder shows the disabledReason', () => {
    renderForm({
      taskId: 'T1',
      disabled: true,
      disabledReason: 'No record for this task on the server.',
    });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveAttribute(
      'placeholder',
      expect.stringContaining('No record for this task'),
    );
  });

  test('Submit button label flips to "Queue" while turnInFlight is true', () => {
    renderForm({ taskId: 'T1', turnInFlight: true });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'follow-up' },
    });
    // Mid-turn the composer queues instead of steering — the button
    // says "Queue" and carries the is-queued accent.
    const submitButton = screen.getByRole('button', { name: /queue/i });
    expect(submitButton).toBeInTheDocument();
    expect(submitButton).toHaveClass('is-queued');
  });

  test('Submit button is "Send" when not in flight', () => {
    renderForm({ taskId: 'T1', turnInFlight: false });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'something' },
    });
    expect(screen.getByRole('button', { name: /^send$/i })).toBeInTheDocument();
  });

  test('Send button is disabled when textarea is empty and no attachments', () => {
    renderForm({ taskId: 'T1' });
    // No text typed, nothing attached. Submit must be disabled to
    // prevent accidental empty-message submission.
    const submitButton = screen.getByRole('button', { name: /^send$/i });
    expect(submitButton).toBeDisabled();
  });

  test('Send button becomes enabled once text is typed', () => {
    renderForm({ taskId: 'T1' });
    const submit = screen.getByRole('button', { name: /^send$/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hi' } });
    expect(submit).not.toBeDisabled();
  });
});


describe('MessageForm — model selector', () => {

  test('renders a model selector when availableModels is non-empty', () => {
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus' },
        { id: 'sonnet', label: 'Sonnet' },
      ],
      selectedModel: 'opus',
    });

    const select = screen.getByRole('combobox', { name: /select model/i });
    expect(select).toBeInTheDocument();
    expect(select).toHaveValue('opus');
  });

  test('does NOT render a model selector when availableModels is empty', () => {
    renderForm({ taskId: 'T1', availableModels: [] });
    expect(screen.queryByRole('combobox', { name: /select model/i }))
      .not.toBeInTheDocument();
  });

  test('with no override, selects the default-flagged model and shows no "Default" option', () => {
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus 4.8' },
        { id: 'sonnet', label: 'Sonnet 4.6', default: true },
        { id: 'haiku', label: 'Haiku' },
      ],
      selectedModel: '',
    });
    const select = screen.getByRole('combobox', { name: /select model/i });
    // The actual default model is shown selected — not an ambiguous "Default".
    expect(select).toHaveValue('sonnet');
    expect(screen.queryByRole('option', { name: 'Default' })).not.toBeInTheDocument();
    const labels = [...select.querySelectorAll('option')].map((o) => o.textContent);
    expect(labels).toEqual(['Opus 4.8', 'Sonnet 4.6', 'Haiku']);
  });

  test('an explicit per-task override wins over the default flag', () => {
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus 4.8' },
        { id: 'sonnet', label: 'Sonnet 4.6', default: true },
      ],
      selectedModel: 'opus',
    });
    expect(screen.getByRole('combobox', { name: /select model/i })).toHaveValue('opus');
  });

  test('changing the selected model fires onModelChange', () => {
    const onModelChange = vi.fn();
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus' },
        { id: 'sonnet', label: 'Sonnet' },
      ],
      selectedModel: 'opus',
      onModelChange,
    });

    fireEvent.change(screen.getByRole('combobox', { name: /select model/i }), {
      target: { value: 'sonnet' },
    });
    expect(onModelChange).toHaveBeenCalledWith('sonnet');
  });
});


describe('MessageForm — effort selector', () => {

  test('renders an effort selector when effortLevels is non-empty', () => {
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'medium', 'high', 'xhigh', 'max'],
      selectedEffort: 'high',
    });
    const select = screen.getByRole('combobox', { name: /select reasoning effort/i });
    expect(select).toBeInTheDocument();
    expect(select).toHaveValue('high');
  });

  test('does NOT render an effort selector when effortLevels is empty', () => {
    renderForm({ taskId: 'T1', effortLevels: [] });
    expect(screen.queryByRole('combobox', { name: /select reasoning effort/i }))
      .not.toBeInTheDocument();
  });

  test('changing the effort fires onEffortChange', () => {
    const onEffortChange = vi.fn();
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'high', 'max'],
      selectedEffort: 'low',
      onEffortChange,
    });
    fireEvent.change(
      screen.getByRole('combobox', { name: /select reasoning effort/i }),
      { target: { value: 'max' } },
    );
    expect(onEffortChange).toHaveBeenCalledWith('max');
  });

  test('has NO "Auto" option — every choice is a concrete level', () => {
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'medium', 'high', 'xhigh', 'max'],
      selectedEffort: '',
      effortDefault: 'high',
    });
    const select = screen.getByRole('combobox', { name: /select reasoning effort/i });
    const optionValues = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    // No empty-value "Auto" option, and the label is gone too.
    expect(optionValues).toEqual(['low', 'medium', 'high', 'xhigh', 'max']);
    expect(screen.queryByText(/Effort: Auto/i)).not.toBeInTheDocument();
  });

  test('with no override, shows the backend default (the level that will run)', () => {
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'medium', 'high', 'xhigh', 'max'],
      selectedEffort: '',
      effortDefault: 'high',
    });
    expect(screen.getByRole('combobox', { name: /select reasoning effort/i }))
      .toHaveValue('high');
  });

  test('an explicit override wins over the default', () => {
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'medium', 'high', 'xhigh', 'max'],
      selectedEffort: 'max',
      effortDefault: 'high',
    });
    expect(screen.getByRole('combobox', { name: /select reasoning effort/i }))
      .toHaveValue('max');
  });

  test('falls back to the first level when neither override nor default is set', () => {
    renderForm({
      taskId: 'T1',
      effortLevels: ['low', 'medium', 'high'],
      selectedEffort: '',
      effortDefault: '',
    });
    expect(screen.getByRole('combobox', { name: /select reasoning effort/i }))
      .toHaveValue('low');
  });
});


describe('MessageForm — composer-height CSS variable (--composer-h)', () => {
  // Operator-reported bug: typing a multi-paragraph message grew
  // the composer past the chat's fixed 120px bottom padding so the
  // last bubbles slipped behind the floating capsule. The fix
  // publishes the composer's current rendered height onto its
  // parent so EventLog's padding-bottom can track it via CSS calc.
  //
  // jsdom's ResizeObserver shim: jsdom doesn't ship ResizeObserver
  // natively. The MessageForm guards on ``typeof ResizeObserver``
  // and falls through to no-op when absent — so we polyfill it here
  // with a minimal stub that lets us observe whether the variable
  // gets published.

  function withResizeObserverStub(run) {
    const original = globalThis.ResizeObserver;
    class Stub {
      constructor(cb) { this._cb = cb; }
      observe() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = Stub;
    try {
      return run();
    } finally {
      if (original === undefined) { delete globalThis.ResizeObserver; }
      else { globalThis.ResizeObserver = original; }
    }
  }

  test('writes --composer-h on the parent element on mount', () => {
    withResizeObserverStub(() => {
      // Render inside a real parent <div> so we can observe the
      // CSS variable being set on it (the component writes to
      // form.parentElement).
      const parent = document.createElement('div');
      document.body.appendChild(parent);
      try {
        render(
          <MessageForm
            taskId="T1" turnInFlight={false} onSubmit={vi.fn()}
          />,
          { container: parent },
        );
        // jsdom returns 0 for offsetHeight on unstyled elements,
        // but the contract is "the property exists" — so the
        // initial publish writes ``0px`` (still a valid value).
        const v = parent.style.getPropertyValue('--composer-h');
        expect(v).toMatch(/^\d+px$/);
      } finally {
        document.body.removeChild(parent);
      }
    });
  });

  test('removes --composer-h on unmount', () => {
    withResizeObserverStub(() => {
      const parent = document.createElement('div');
      document.body.appendChild(parent);
      try {
        const { unmount } = render(
          <MessageForm
            taskId="T1" turnInFlight={false} onSubmit={vi.fn()}
          />,
          { container: parent },
        );
        expect(parent.style.getPropertyValue('--composer-h')).toMatch(/^\d+px$/);
        unmount();
        // Cleanup must remove the var so the next mount starts
        // from a known state (and the CSS fallback re-engages).
        expect(parent.style.getPropertyValue('--composer-h')).toBe('');
      } finally {
        document.body.removeChild(parent);
      }
    });
  });

  test('no-op gracefully when ResizeObserver is unavailable', () => {
    // Environments without ResizeObserver (very old browsers, some
    // jsdom configs) must not crash on mount — the CSS fallback
    // (padding-bottom: calc(var(--composer-h, 94px) + 28px))
    // still keeps the last bubble visible at the default sizing.
    const original = globalThis.ResizeObserver;
    delete globalThis.ResizeObserver;
    try {
      const parent = document.createElement('div');
      document.body.appendChild(parent);
      try {
        // Must not throw.
        render(
          <MessageForm
            taskId="T1" turnInFlight={false} onSubmit={vi.fn()}
          />,
          { container: parent },
        );
        expect(parent.style.getPropertyValue('--composer-h')).toBe('');
      } finally {
        document.body.removeChild(parent);
      }
    } finally {
      if (original !== undefined) { globalThis.ResizeObserver = original; }
    }
  });
});


describe('MessageForm — server draft persistence (.kato-prompts.json)', () => {
  beforeEach(() => {
    _serverDrafts.clear();
    _idbMem.clear();
    window.localStorage.clear();
  });

  test('restores text from the server when the browser cache is empty', async () => {
    _serverDrafts.set('T1', { text: 'recovered from server', images: [] });
    renderForm({ taskId: 'T1' });
    // Async server read fills the empty composer.
    expect(await screen.findByDisplayValue('recovered from server')).toBeInTheDocument();
  });

  test('restores images from the server when the browser cache is empty', async () => {
    _serverDrafts.set('T1', { text: '', images: [{ media_type: 'image/png', data: 'AAAA' }] });
    const { container } = renderForm({ taskId: 'T1' });
    await waitFor(() => {
      expect(container.querySelector('.message-attachment img')).toBeTruthy();
    });
    expect(container.querySelector('.message-attachment img'))
      .toHaveAttribute('src', 'data:image/png;base64,AAAA');
  });

  test('a browser-cache draft wins — server only fills what is empty', async () => {
    window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}T1`, 'local draft');
    _serverDrafts.set('T1', { text: 'server draft', images: [] });
    renderForm({ taskId: 'T1' });
    // localStorage seeds instantly; the later server read must NOT overwrite it.
    expect(screen.getByRole('textbox')).toHaveValue('local draft');
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('textbox')).toHaveValue('local draft');
  });

  test('sending clears the server draft', async () => {
    _serverDrafts.set('T1', { text: 'queued thought', images: [] });
    const onSubmit = vi.fn().mockResolvedValue(true);
    const { container } = renderForm({ taskId: 'T1', onSubmit });
    expect(await screen.findByDisplayValue('queued thought')).toBeInTheDocument();
    await act(async () => { fireEvent.submit(container.querySelector('form')); });
    expect(onSubmit).toHaveBeenCalled();
    expect(_serverDrafts.has('T1')).toBe(false);  // server draft cleared on send
  });

  test('typing is mirrored to the server (debounced)', async () => {
    vi.useFakeTimers();
    try {
      renderForm({ taskId: 'T1' });
      // let the mount server-read resolve (arms the save), then type.
      await vi.runOnlyPendingTimersAsync();
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'persist me' } });
      expect(_serverDrafts.has('T1')).toBe(false);  // not yet — debounced
      await vi.advanceTimersByTimeAsync(600);
      expect(_serverDrafts.get('T1')).toEqual({ text: 'persist me', images: [] });
    } finally {
      vi.useRealTimers();
    }
  });
});
