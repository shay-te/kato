import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useAgentVersion } from '../hooks/useAgentVersion.js';
import { useTaskTree } from '../stores/taskCache/index.js';
import ComposerMentionMenu from './ComposerMentionMenu.jsx';
import {
  applyMention,
  detectMentionQuery,
  filterMentionFiles,
  flattenTreeFiles,
  referenceFor,
} from '../utils/composerMentions.js';
import {
  collectImageParts,
  IMAGE_REJECT_REASON,
} from '../utils/imageAttachment.js';
import {
  looksLikeTextFile,
  readTextAttachment,
  formatTextAttachment,
  TEXT_REJECT_REASON,
} from '../utils/textAttachment.js';
import { toast } from '../stores/toastStore.js';
import { useAutoSizeTextarea } from '../hooks/useAutoSizeTextarea.js';
import { usePublishedHeight } from '../hooks/usePublishedHeight.js';
import { appendComposerFragment } from '../utils/chatComposerHelpers.js';
import {
  readDraft,
  writeDraft,
  readUltracode,
  writeUltracode,
} from '../utils/composerDraft.js';
import {
  readImageDraft,
  writeImageDraft,
  clearImageDraft,
} from '../utils/composerImageDraft.js';
import { fetchDraft, saveDraft } from '../api.js';
import ComposerActionsMenu from './ComposerActionsMenu.jsx';
import ComposerModeMenu from './ComposerModeMenu.jsx';
import ContextMeter from './ContextMeter.jsx';

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

// Composer @-mention (workspace file picker) — closed state + dropdown cap so a
// huge repo never renders thousands of rows.
const MENTION_CLOSED = { active: false, query: '', start: -1, index: 0 };
const MENTION_LIMIT = 50;

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
  effortDefault = '',
  onEffortChange,
  planMode = false,
  onPlanModeChange,
  planAvailable = false,
  onOpenPlan,
  agentMode = '',
  onAgentModeChange,
  contextUsage = null,
  onStop,
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
  // "ultracode" opt-in: kato has no such setting — it's a Claude Code
  // workflow-mode PROMPT KEYWORD. When on, we prepend it to the message so the
  // agent authors/runs multi-agent workflows (only takes effect if the spawned
  // Claude supports it). Off by default — it can trigger expensive fan-out.
  // Persisted per-task in localStorage (same idiom as the text draft) so the
  // toggle survives tab switches and page reloads.
  const [ultracode, setUltracode] = useState(() => readUltracode(taskId));
  // Only offer ultracode when the installed agent CLI actually supports
  // multi-agent workflows — otherwise the keyword is inert and the toggle
  // misleads. ``null`` (still loading) keeps it hidden until confirmed.
  const agentVersion = useAgentVersion();
  const supportsWorkflows = !!(agentVersion && agentVersion.supports_workflows);
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  const formRef = useRef(null);
  const pendingCaretRef = useRef(null);

  // @-mention file picker: typing "@" in the composer opens a dropdown of
  // workspace files. The tree is the SAME data the Files tab shows (cached in
  // the task store), so opening the picker never triggers a fetch; we just
  // flatten it to a file list once per tree change.
  const [mention, setMention] = useState(MENTION_CLOSED);
  const { trees } = useTaskTree(taskId);
  const mentionFiles = useMemo(() => flattenTreeFiles(trees), [trees]);
  const mentionMatches = useMemo(
    () => (mention.active
      ? filterMentionFiles(mentionFiles, mention.query, MENTION_LIMIT)
      : []),
    [mention.active, mention.query, mentionFiles],
  );
  const mentionMenuOpen = mention.active && mentionMatches.length > 0;

  // "Live state now owns the draft." Set after any local mutation that
  // supersedes the server draft (clear, send, paste-images, remove-image):
  // blocks a slow in-flight hydrate read (issued at mount) from re-applying
  // a stale prompt over what the operator just did. Folded into one call so
  // the three refs can't drift out of sync across the (previously copy-pasted)
  // mutation sites.
  function markDraftSettled() {
    imagesSettledRef.current = true;
    draftEditedRef.current = true;
    draftSyncReadyRef.current = true;
  }

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

  useEffect(() => {
    writeUltracode(taskId, ultracode);
  }, [taskId, ultracode]);

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
      markDraftSettled(); // live state owns the draft; block a late hydrate
      setValue('');
      setAttachments([]);
      writeDraft(taskId, '');
      clearImageDraft(taskId);
      saveDraft(taskId, { text: '', images: [] }); // clear the server draft now
    },
    getValue() { return value; },
  }), [taskId, value]);

  // Put the composer text + images back after a FAILED send so the operator
  // can retry — the optimistic clear in submit() wiped them the instant we
  // sent. (Losing text on a network failure was a real operator pain point.)
  function restoreComposer(text, atts) {
    setValue(text);
    setAttachments(atts);
    writeDraft(taskId, text);
    writeImageDraft(taskId, atts.map((a) => a.part));
    saveDraft(taskId, { text, images: atts.map((a) => a.part) });
  }

  async function submit(event) {
    event.preventDefault();
    if (disabled) { return; }
    const trimmed = (value || '').trim();
    if (!trimmed && attachments.length === 0) { return; }
    // Prepend the ultracode keyword only when the toggle is on, the CLI
    // actually supports workflows, and there's text — so a stale localStorage
    // toggle never injects an inert keyword on an unsupported CLI.
    const outgoing = ultracode && supportsWorkflows && trimmed
      ? `ultracode\n\n${trimmed}`
      : trimmed;
    // Clear the composer IMMEDIATELY: the message is already shown in the
    // chat, so it shouldn't linger in the input for the send round-trip.
    // Capture first so a failed send can restore it for retry.
    // markDraftSettled() blocks an in-flight mount hydrate (slow to resolve)
    // from re-applying the just-sent images.
    const sentText = value;
    const sentAttachments = attachments;
    markDraftSettled();
    setMention(MENTION_CLOSED);
    setValue('');
    setAttachments([]);
    writeDraft(taskId, '');
    clearImageDraft(taskId);
    saveDraft(taskId, { text: '', images: [] });

    let result;
    try {
      result = await onSubmit(outgoing, sentAttachments.map((a) => a.part));
    } catch (_err) {
      // Send threw — caller surfaced an error bubble. Bring the draft back.
      restoreComposer(sentText, sentAttachments);
      return;
    }
    // Explicit ``false`` = "send failed" without throw → restore for retry.
    // Anything else (undefined / true) is success; the composer stays cleared.
    if (result === false) { restoreComposer(sentText, sentAttachments); }
  }

  // Slash commands go to the session as-is: they are instructions to the
  // CLI, not prose, so they carry no attachments and skip the ultracode
  // prefix (which would turn `/compact` into unrecognised text). The draft
  // is left untouched — running /cost shouldn't cost you a half-written
  // message.
  async function runCommand(command) {
    if (typeof onSubmit !== 'function' || disabled) { return; }
    try {
      await onSubmit(command, []);
    } catch (_err) {
      // Send threw — SessionDetail surfaced the error bubble. Nothing to
      // restore: the composer's own draft was never consumed.
    }
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
  // While Claude is working with NOTHING to send, the button has no job — it
  // sat there disabled. That is exactly the moment you want to interrupt, so
  // it becomes Stop instead. With text or attachments present it stays the
  // queue button: typing a follow-up must never turn into killing the turn.
  const showStop = Boolean(
    turnInFlight && !disabled && !hasContent && typeof onStop === 'function',
  );
  const submitLabel = isQueueing ? 'Queue' : 'Send';
  let submitTitle;
  if (disabled) {
    submitTitle = disabledReason || 'Session is not live — chat resumes when kato re-spawns it.';
  } else if (turnInFlight) {
    submitTitle = 'Claude is working — your message will be queued and sent when the turn finishes.';
  } else {
    submitTitle = 'Send your message to Claude (or press Enter).';
  }

  // Recompute the @-mention state from the live text + caret. Resets the
  // highlighted row to the top only when the query actually changes, so arrowing
  // through the menu (which doesn't change the text) keeps your place.
  function syncMention(text, caret) {
    const detected = detectMentionQuery(text, caret);
    setMention((prev) => {
      if (!detected.active) {
        return prev.active ? MENTION_CLOSED : prev;
      }
      if (prev.active && prev.query === detected.query && prev.start === detected.start) {
        return prev;
      }
      return { active: true, query: detected.query, start: detected.start, index: 0 };
    });
  }

  function closeMention() {
    setMention((prev) => (prev.active ? MENTION_CLOSED : prev));
  }

  // Insert the picked file's repo-scoped reference in place of the "@query"
  // (same format as a Files-tab click), then restore the caret after it.
  function selectMention(file) {
    if (!file) { return; }
    const to = mention.start + 1 + mention.query.length;
    const next = applyMention(value, mention.start, to, referenceFor(file));
    markDraftSettled(); // live state owns the draft; block a late hydrate
    pendingCaretRef.current = next.caret;
    setValue(next.text);
    setMention(MENTION_CLOSED);
  }

  function handleChange(event) {
    // The operator is editing — a still-in-flight server read must not clobber
    // this, and the draft is now safe to write back to the server.
    draftEditedRef.current = true;
    draftSyncReadyRef.current = true;
    setValue(event.target.value);
    syncMention(event.target.value, event.target.selectionStart);
  }
  function handleKeyDown(event) {
    // While the @-file picker is open it OWNS arrow/enter/tab/escape so they
    // navigate + pick instead of moving the caret or sending the message.
    if (mention.active && event.key === 'Escape') {
      event.preventDefault();
      closeMention();
      return;
    }
    if (mentionMenuOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const n = mentionMatches.length;
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        setMention((m) => ({ ...m, index: (m.index + delta + n) % n }));
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        selectMention(mentionMatches[mention.index] || mentionMatches[0]);
        return;
      }
    }
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
    await ingestFiles(files);
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
    await ingestFiles(files);
  }

  // Route dropped / picked files: images become attachment thumbnails,
  // text-based files (xml, json, yaml, plain/extensionless text…) get their
  // content inlined into the composer as a fenced block. Everything else is
  // rejected. Keeps the operator from hitting "Only PNG… supported" for a
  // config/data file they legitimately want to hand the agent.
  async function ingestFiles(files) {
    const images = [];
    const textFiles = [];
    for (const file of files || []) {
      if (looksLikeTextFile(file)) { textFiles.push(file); }
      else { images.push(file); }
    }
    if (images.length > 0) { await ingestImages(images); }
    if (textFiles.length > 0) { await ingestTextFiles(textFiles); }
  }

  async function ingestTextFiles(files) {
    const blocks = [];
    for (const file of files) {
      // eslint-disable-next-line no-await-in-loop
      const { text, truncated, reason } = await readTextAttachment(file);
      if (text != null) {
        blocks.push(formatTextAttachment(file.name, text, { truncated }));
        if (truncated) {
          toast.show({
            kind: 'warning',
            title: 'Large file truncated',
            message: `${file.name} was truncated — only the first part was attached.`,
            durationMs: 6000,
          });
        }
      } else {
        toast.show({
          kind: reason === TEXT_REJECT_REASON.BINARY ? 'warning' : 'error',
          title: 'File attachment rejected',
          message: _textRejectionMessage(reason, file.name),
          durationMs: 6000,
        });
      }
    }
    if (blocks.length === 0) { return; }
    // Inline via the same fragment path as "Place in chat" — the live state
    // now owns the draft, so a still-in-flight hydrate can't clobber it.
    markDraftSettled();
    setValue((current) => {
      let next = current;
      for (const block of blocks) { next = appendComposerFragment(next, block); }
      pendingCaretRef.current = next.length;
      return next;
    });
  }

  async function ingestImages(items) {
    const { parts, rejections } = await collectImageParts(items, {
      existingCount: attachments.length,
    });
    if (parts.length > 0) {
      // The operator is actively attaching — the live state owns the draft, so
      // a still-in-flight hydrate read must not clobber it.
      markDraftSettled();
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
    markDraftSettled(); // live state owns the draft
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
      <div className="composer-input-wrap">
        {mention.active && (
          <ComposerMentionMenu
            items={mentionMatches}
            activeIndex={Math.min(mention.index, Math.max(0, mentionMatches.length - 1))}
            onSelect={selectMention}
            onHover={(index) => setMention((m) => ({ ...m, index }))}
          />
        )}
        <textarea
          ref={textareaRef}
          id="message-input"
          placeholder={placeholder}
          rows={1}
          title="Shift+Enter for newline. Type @ to tag a workspace file. Paste or drop images to attach."
          value={value || ''}
          disabled={disabled}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={closeMention}
        />
      </div>
      <div className="composer-toolbar">
        <div className="composer-toolbar-left">
          <button
            type="button"
            id="message-attach"
            className="tooltip-above"
            data-tooltip="Attach files — images (PNG/JPEG/GIF/WebP) show as thumbnails; text files (json, xml, yaml, logs, or any plain-text/extensionless file) are inlined into your message. Paste, drop, or click to pick."
            disabled={disabled}
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach files"
          >
            <span aria-hidden="true">+</span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={handleFilePickerChange}
          />
          <ComposerActionsMenu
            onRun={runCommand}
            disabled={disabled}
            models={availableModels}
            selectedModel={effectiveModelId(availableModels, selectedModel)}
            onModelChange={onModelChange}
            effortLevels={effortLevels}
            selectedEffort={effectiveEffort(effortLevels, selectedEffort, effortDefault)}
            onEffortChange={onEffortChange}
          />
        </div>
        <div className="composer-toolbar-right">
          <ContextMeter usage={contextUsage} />
          <ComposerModeMenu
            mode={agentMode}
            onChange={onAgentModeChange}
            disabled={disabled}
            ultracode={ultracode}
            onUltracodeChange={setUltracode}
            supportsWorkflows={supportsWorkflows}
            planAvailable={planAvailable}
            onOpenPlan={onOpenPlan}
          />
          {showStop ? (
            <button
              type="button"
              className="message-send is-stop tooltip-above"
              data-tooltip="Stop Claude. The turn ends where it is and the chat history is kept — this does not start a new session."
              aria-label="Stop Claude"
              onClick={() => onStop && onStop()}
            >
              <span aria-hidden="true">■</span>
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled || !hasContent}
              className={`message-send ${submitClass} tooltip-above`.trim()}
              data-tooltip={submitTitle}
              aria-label={submitLabel}
            >
              <span aria-hidden="true">{isQueueing ? '◴' : '↑'}</span>
            </button>
          )}
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


// The effort dropdown shows a concrete level, never an ambiguous "Auto":
// with no per-task override we select the backend's reported default (the
// level kato actually passes to --effort when the operator hasn't chosen
// one), falling back to the first advertised level. So the operator always
// sees the effort that will run rather than the word "Auto".
function effectiveEffort(levels, selected, fallback) {
  if (selected) {
    return selected;
  }
  if (fallback && levels.includes(fallback)) {
    return fallback;
  }
  return levels[0] || '';
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

function _textRejectionMessage(reason, name) {
  const label = name ? `"${name}"` : 'This file';
  switch (reason) {
    case TEXT_REJECT_REASON.BINARY:
      return `${label} looks like a binary file — attach images as PNG/JPEG/GIF/WebP, or paste text directly.`;
    case TEXT_REJECT_REASON.EMPTY:
      return `${label} is empty.`;
    case TEXT_REJECT_REASON.READ_FAILED:
      return `Could not read ${label}.`;
    default:
      return `${label} could not be attached.`;
  }
}
