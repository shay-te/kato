import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  collectImageParts,
  IMAGE_REJECT_REASON,
} from '../utils/imageAttachment.js';
import { toast } from '../stores/toastStore.js';
import { useAutoSizeTextarea } from '../hooks/useAutoSizeTextarea.js';
import { usePublishedHeight } from '../hooks/usePublishedHeight.js';
import { appendComposerFragment } from '../utils/chatComposerHelpers.js';
import { readDraft, writeDraft } from '../utils/composerDraft.js';
import {
  readImageDraft,
  writeImageDraft,
  clearImageDraft,
} from '../utils/composerImageDraft.js';
import { fetchDraft, saveDraft } from '../api.js';

// Composer state (the textarea contents + attached images) lives
// INSIDE this component on purpose — typing should not re-render
// the rest of the UI tree. Earlier the value was lifted to App so
// every keystroke walked the entire tab list, the EventLog, the
// FilesTab tree, and the ChangesTab diff (with comment widgets).
// Multiply that by typing speed and the operator saw visible
// per-keystroke lag on busy tabs.
//
// Now App holds a ref to this component (forwarded via the ref
// arg) and reaches in imperatively when it needs to push a
// fragment ("paste this file path / repo:path snippet into the
// composer"). Typing stays local; appendFragment is rare; both
// paths stay correct without an O(tree) re-render.
//
// Draft text survives tab switches via localStorage keyed by
// ``taskId`` — see ``utils/composerDraft.js`` for the pure helpers.
// SessionDetail keys this component on ``activeTaskId``, so React
// unmounts it when the operator switches tabs and the in-memory
// ``value`` state is dropped. Persisting to localStorage on every
// keystroke lets the next mount (on tab return) read the in-progress
// draft back out — matches VS Code's per-tab draft behaviour.
// Submit / clear / mount-on-empty all wipe the key.
const SINGLE_LINE_TEXTAREA_HEIGHT = 'calc(1.4em + 16px)';

const MessageForm = forwardRef(function MessageForm({
  taskId,
  turnInFlight,
  onSubmit,
  disabled = false,
  disabledReason = '',
  availableModels = [],
  selectedModel = '',
  onModelChange,
  effortLevels = [],
  selectedEffort = '',
  onEffortChange,
}, ref) {
  // Lazy initializer reads the persisted draft once on mount.
  // SessionDetail keys this component on the active task, so this
  // hydrates correctly when the operator tabs back to the task.
  const [value, setValue] = useState(() => readDraft(taskId));
  // Attached images live in component state because the composer is the only
  // thing that reads / writes them. They're ALSO mirrored to IndexedDB
  // (composerImageDraft) per task — base64 image data is too big for
  // localStorage but fits IndexedDB — so a pasted/dropped image survives both
  // a tab switch and a full page reload, just like the text draft. Hydration
  // is async (IDB), so the initial state is empty and an effect fills it.
  const [attachments, setAttachments] = useState([]);
  // ``imagesSettledRef`` means "the live attachments now own the draft" — set
  // true once the async hydrate has run OR the operator has touched
  // attachments (paste / remove / send / clear), whichever comes first. It does
  // double duty: (1) it gates the persist effect so the empty pre-hydrate state
  // can't wipe the stored draft, and (2) it makes a slow IndexedDB read that
  // resolves AFTER a send/clear refuse to re-apply (otherwise an in-flight read
  // would resurrect a just-sent image — the `length === 0` check alone can't
  // tell "cold" from "just emptied").
  const imagesSettledRef = useRef(false);
  // Server-side draft (text + images) at <workspace>/.kato-prompts.json is the
  // DURABLE backstop: localStorage/IndexedDB are per-browser and get wiped by
  // private mode / cleared data, so a refresh can still lose the prompt. The
  // server file survives a refresh, a different browser, and task switches.
  // ``draftEditedRef`` = the operator has edited this draft since mount (so a
  // slow server read must NOT clobber live text / a just-sent empty). ``draft
  // SyncReadyRef`` = safe to write to the server (the read resolved OR the
  // operator edited) — gates the save so the empty pre-hydrate state can't wipe
  // the stored draft.
  const draftEditedRef = useRef(false);
  const draftSyncReadyRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  const formRef = useRef(null);
  const pendingCaretRef = useRef(null);

  // Auto-grow the textarea on every value change (typing, draft
  // hydration, fragment paste). The hook resets to a single-line
  // height when the draft is empty and returns the resize fn so the
  // caret-restoration effect below can call it imperatively.
  const autoResize = useAutoSizeTextarea(textareaRef, value, {
    emptyHeight: SINGLE_LINE_TEXTAREA_HEIGHT,
  });

  // Publish the composer's live height as ``--composer-h`` on the parent
  // (#session-detail) so #event-log reserves bottom room and the last
  // bubble / working indicator never slips behind the floating capsule.
  usePublishedHeight('--composer-h', formRef);

  useLayoutEffect(() => {
    const caret = pendingCaretRef.current;
    if (caret == null) { return; }
    pendingCaretRef.current = null;
    const el = textareaRef.current;
    if (!el) { return; }
    autoResize();
    try {
      el.focus({ preventScroll: true });
    } catch (_err) {
      el.focus();
    }
    el.setSelectionRange(caret, caret);
    el.scrollTop = el.scrollHeight;
  }, [value, autoResize]);

  // Mirror every text change into localStorage so the next mount
  // (on tab return) hydrates with the same in-progress draft.
  useEffect(() => {
    writeDraft(taskId, value);
  }, [taskId, value]);

  // Restore the draft from the SERVER on mount — the durable backstop for when
  // the browser caches were wiped (private mode, cleared data, a different
  // browser). Fill text/images ONLY where the composer is still empty (so a
  // browser-cache value wins), and never when the operator has already edited
  // this draft (so a slow read can't clobber live typing or a just-sent empty).
  useEffect(() => {
    let cancelled = false;
    fetchDraft(taskId).then((draft) => {
      if (cancelled) { return; }
      if (!draftEditedRef.current) {
        if (draft && draft.text) {
          setValue((current) => (current ? current : draft.text));
        }
        if (draft && Array.isArray(draft.images) && draft.images.length > 0) {
          setAttachments((current) => (current.length
            ? current
            : draft.images.map((part) => ({ part, previewUrl: _previewUrl(part) }))));
        }
      }
      draftSyncReadyRef.current = true;
    });
    return () => { cancelled = true; };
  }, [taskId]);

  // Mirror the draft to the server (debounced — text changes per keystroke),
  // once it's safe (the read resolved or the operator edited). On send/clear we
  // also write through immediately (below) so the server clears without waiting.
  useEffect(() => {
    if (!draftSyncReadyRef.current) { return undefined; }
    const timer = setTimeout(() => {
      saveDraft(taskId, { text: value, images: attachments.map((a) => a.part) });
    }, 500);
    return () => clearTimeout(timer);
  }, [taskId, value, attachments]);

  // Restore persisted image attachments (IndexedDB) on mount — survives tab
  // switch AND full reload. Rebuild each preview URL from its stored part so we
  // never persisted a throwaway object URL. If the operator already touched
  // attachments (incl. a send/clear) while this async read was in flight,
  // ``imagesSettledRef`` is already true and we DON'T clobber the live state.
  useEffect(() => {
    let cancelled = false;
    readImageDraft(taskId).then((parts) => {
      if (cancelled || imagesSettledRef.current) { return; }
      if (parts.length > 0) {
        setAttachments(parts.map((part) => ({ part, previewUrl: _previewUrl(part) })));
      }
      imagesSettledRef.current = true;
    });
    return () => { cancelled = true; };
  }, [taskId]);

  // Mirror attachment changes into IndexedDB, but only once the live state owns
  // the draft (see imagesSettledRef) so the pre-hydrate empty state can't wipe
  // a stored draft on a fresh mount.
  useEffect(() => {
    if (imagesSettledRef.current) {
      writeImageDraft(taskId, attachments.map((a) => a.part));
    }
  }, [taskId, attachments]);

  // Expose the imperative API App uses for "paste this fragment"
  // (file-tree clicks, Cmd+P picker results, diff right-click,
  // commit-id paste). Stable per-mount: the parent's
  // ``appendToInput`` callback never changes.
  useImperativeHandle(ref, () => ({
    appendFragment(fragment) {
      setValue((current) => {
        const next = appendComposerFragment(current, fragment);
        pendingCaretRef.current = next.length;
        return next;
      });
    },
    clear() {
      imagesSettledRef.current = true; // live state owns the draft; block a late hydrate
      draftEditedRef.current = true;
      draftSyncReadyRef.current = true;
      setValue('');
      setAttachments([]);
      writeDraft(taskId, '');
      clearImageDraft(taskId);
      saveDraft(taskId, { text: '', images: [] }); // clear the server draft now
    },
    getValue() { return value; },
  }), [taskId, value]);

  async function submit(event) {
    event.preventDefault();
    if (disabled) { return; }
    const trimmed = (value || '').trim();
    if (!trimmed && attachments.length === 0) { return; }
    // AWAIT onSubmit and only clear local state on a truthy result
    // (or undefined — back-compat with callers that return nothing
    // but never throw). If the send failed, KEEP the draft so the
    // operator can retry — losing the text on a network failure
    // was a real operator pain point.
    let result;
    try {
      result = await onSubmit(trimmed, attachments.map((a) => a.part));
    } catch (_err) {
      // Send threw — caller will have surfaced an error bubble.
      // Preserve the draft + textarea so the operator can retry.
      return;
    }
    // Explicit ``false`` return signals "send failed" without throw;
    // keep the draft. Anything else (including undefined / true) is
    // treated as success.
    if (result === false) { return; }
    // The live state now owns the draft: this blocks an in-flight hydrate read
    // (issued at mount, slow to resolve) from re-applying the just-sent images.
    imagesSettledRef.current = true;
    draftEditedRef.current = true;
    draftSyncReadyRef.current = true;
    setValue('');
    setAttachments([]);
    writeDraft(taskId, '');
    // Clear the persisted draft immediately (not via the debounced effect) so an
    // unmount right after sending can't leave already-sent text/images to be
    // re-hydrated on return — browser caches AND the server file.
    clearImageDraft(taskId);
    saveDraft(taskId, { text: '', images: [] });
  }

  // While Claude is working the composer is in QUEUE mode: the
  // message is held and auto-sent by SessionDetail when the current
  // turn finishes (no mid-turn steering).
  const isQueueing = turnInFlight && !disabled;
  const placeholder = disabled
    ? disabledReason || 'Session is not live — chat resumes when kato re-spawns it.'
    : isQueueing
      ? 'Queue another message… (sends when Claude is free)'
      : 'Reply to Claude';
  const submitClass = isQueueing ? 'is-queued' : '';
  const hasContent = (value || '').trim() || attachments.length > 0;
  const submitLabel = isQueueing ? 'Queue' : 'Send';
  let submitTitle;
  if (disabled) {
    submitTitle = disabledReason || 'Session is not live — chat resumes when kato re-spawns it.';
  } else if (turnInFlight) {
    submitTitle = 'Claude is working — your message will be queued and sent when the turn finishes.';
  } else {
    submitTitle = 'Send your message to Claude (or press Enter).';
  }

  function handleChange(event) {
    // The operator is editing — a still-in-flight server read must not clobber
    // this, and the draft is now safe to write back to the server.
    draftEditedRef.current = true;
    draftSyncReadyRef.current = true;
    setValue(event.target.value);
  }
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      submit(event);
    }
  }

  async function handlePaste(event) {
    if (disabled) { return; }
    const items = Array.from(event.clipboardData?.items || []);
    const imageItems = items.filter((it) => it.type && it.type.startsWith('image/'));
    if (imageItems.length === 0) { return; }
    // Stop the textarea from inserting a "filename"/blob placeholder
    // when the clipboard has both text and an image.
    event.preventDefault();
    await ingestImages(imageItems);
  }

  async function handleFilePickerChange(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (files.length === 0) { return; }
    await ingestImages(files);
  }

  function handleDragEnter(event) {
    if (disabled) { return; }
    if (!event.dataTransfer || !event.dataTransfer.types) { return; }
    if (Array.from(event.dataTransfer.types).includes('Files')) {
      event.preventDefault();
      setDragging(true);
    }
  }
  function handleDragLeave() { setDragging(false); }
  function handleDragOver(event) {
    if (disabled) { return; }
    if (!event.dataTransfer || !event.dataTransfer.types) { return; }
    if (Array.from(event.dataTransfer.types).includes('Files')) {
      event.preventDefault();
    }
  }
  async function handleDrop(event) {
    if (disabled) { return; }
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length === 0) { return; }
    await ingestImages(files);
  }

  async function ingestImages(items) {
    const { parts, rejections } = await collectImageParts(items, {
      existingCount: attachments.length,
    });
    if (parts.length > 0) {
      // The operator is actively attaching — the live state owns the draft, so
      // a still-in-flight hydrate read must not clobber it.
      imagesSettledRef.current = true;
      draftEditedRef.current = true;
      draftSyncReadyRef.current = true;
      const next = parts.map((part) => ({ part, previewUrl: _previewUrl(part) }));
      setAttachments((prev) => [...prev, ...next]);
    }
    for (const rejection of rejections) {
      toast.show({
        kind: rejection.reason === IMAGE_REJECT_REASON.UNSUPPORTED_TYPE ? 'warning' : 'error',
        title: 'Image attachment rejected',
        message: _rejectionMessage(rejection.reason),
        durationMs: 6000,
      });
    }
  }

  function removeAttachment(index) {
    imagesSettledRef.current = true; // live state owns the draft
    draftEditedRef.current = true;
    draftSyncReadyRef.current = true;
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <form
      ref={formRef}
      id="message-form"
      onSubmit={submit}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      className={dragging ? 'is-drop-target' : ''}
    >
      {attachments.length > 0 && (
        <div className="message-attachments">
          {attachments.map((attachment, index) => (
            <div key={index} className="message-attachment">
              <img src={attachment.previewUrl} alt="" />
              <button
                type="button"
                className="message-attachment-remove"
                onClick={() => removeAttachment(index)}
                aria-label="Remove attachment"
                title="Remove"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <textarea
        ref={textareaRef}
        id="message-input"
        placeholder={placeholder}
        rows={1}
        title="Shift+Enter for newline. Paste or drop images to attach."
        value={value || ''}
        disabled={disabled}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
      <div className="composer-toolbar">
        <div className="composer-toolbar-left">
          <button
            type="button"
            id="message-attach"
            className="tooltip-above"
            data-tooltip="Attach images — paste a screenshot, drop a file, or click to pick."
            disabled={disabled}
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach images"
          >
            <span aria-hidden="true">+</span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            multiple
            style={{ display: 'none' }}
            onChange={handleFilePickerChange}
          />
        </div>
        <div className="composer-toolbar-right">
          {availableModels.length > 0 && (
            <ComposerSelect
              id="model-selector"
              tooltip="Model used for the next session spawn. Takes effect when Claude is re-spawned."
              ariaLabel="Select model"
              value={effectiveModelId(availableModels, selectedModel)}
              onChange={onModelChange}
            >
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </ComposerSelect>
          )}
          {effortLevels.length > 0 && (
            <ComposerSelect
              id="effort-selector"
              tooltip="Reasoning effort for this chat. Higher = more thinking. 'Auto' uses the configured default. A change applies on the next message (the session re-spawns to take effect)."
              ariaLabel="Select reasoning effort"
              value={selectedEffort}
              onChange={onEffortChange}
            >
              <option value="">Effort: Auto</option>
              {effortLevels.map((level) => (
                <option key={level} value={level}>{`Effort: ${level}`}</option>
              ))}
            </ComposerSelect>
          )}
          <button
            type="submit"
            disabled={disabled || !hasContent}
            className={`message-send ${submitClass} tooltip-above`.trim()}
            data-tooltip={submitTitle}
            aria-label={submitLabel}
          >
            <span aria-hidden="true">{isQueueing ? '◴' : '↑'}</span>
          </button>
        </div>
      </div>
    </form>
  );
});


export default MessageForm;


// Shared composer dropdown. The model picker and the effort picker
// are the same control with different options, so they render through
// one component — identical markup, identical ``.composer-select``
// styling (see app.scss). Keep the per-instance ``id`` for tests and
// value targeting; everything visual lives on the shared class.
// The model dropdown shows a concrete model, never an ambiguous "Default":
// with no per-task override we select the model the backend flags as the
// default (``default: true`` — the one spawn actually falls back to), so the
// operator always sees the model that will run rather than the word "Default".
function effectiveModelId(models, selected) {
  if (selected) {
    return selected;
  }
  const flagged = models.find((m) => m.default);
  return (flagged || models[0] || {}).id || '';
}


function ComposerSelect({ id, value, onChange, tooltip, ariaLabel, children }) {
  return (
    <select
      id={id}
      className="composer-select tooltip-above"
      data-tooltip={tooltip}
      value={value}
      onChange={(e) => onChange && onChange(e.target.value)}
      aria-label={ariaLabel}
    >
      {children}
    </select>
  );
}


function _previewUrl(part) {
  // Already-base64; embed directly so React's <img> can render it
  // without having to round-trip through createObjectURL.
  return `data:${part.media_type};base64,${part.data}`;
}


function _rejectionMessage(reason) {
  switch (reason) {
    case IMAGE_REJECT_REASON.UNSUPPORTED_TYPE:
      return 'Only PNG, JPEG, GIF, and WebP are supported.';
    case IMAGE_REJECT_REASON.TOO_LARGE:
      return 'Image is too large (max 5 MB per image).';
    case IMAGE_REJECT_REASON.TOO_MANY:
      return 'Max 10 images per message.';
    case IMAGE_REJECT_REASON.READ_FAILED:
      return 'Could not read the image.';
    default:
      return 'Image rejected.';
  }
}
