import { useRef, useState } from 'react';
import {
  collectImageParts,
  IMAGE_REJECT_REASON,
} from '../utils/imageAttachment.js';
import { toast } from '../stores/toastStore.js';

export default function MessageForm({
  value,
  onChange,
  turnInFlight,
  onSubmit,
  disabled = false,
  disabledReason = '',
}) {
  // Attached images live in component state (not lifted) because the
  // composer is the only thing that reads / writes them — no other
  // pane needs to know what the operator pasted before they hit Send.
  const [attachments, setAttachments] = useState([]);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  function submit(event) {
    event.preventDefault();
    if (disabled) { return; }
    const trimmed = (value || '').trim();
    if (!trimmed && attachments.length === 0) { return; }
    onSubmit(trimmed, attachments.map((a) => a.part));
    onChange('');
    setAttachments([]);
  }

  const placeholder = disabled
    ? disabledReason || 'Session is not live — chat resumes when kato re-spawns it.'
    : 'Reply to Claude (Shift+Enter for newline, paste / drop / 📎 for images)';
  const isSteering = turnInFlight && !disabled;
  const submitClass = isSteering ? 'is-steering' : '';
  const hasContent = (value || '').trim() || attachments.length > 0;
  const submitLabel = isSteering ? 'Steer' : 'Send';
  let submitTitle;
  if (disabled) {
    submitTitle = disabledReason || 'Session is not live — chat resumes when kato re-spawns it.';
  } else if (turnInFlight) {
    submitTitle = 'Claude is working — your message will steer the in-flight turn.';
  } else {
    submitTitle = 'Send your message to Claude (or press Enter).';
  }

  function handleChange(event) {
    onChange(event.target.value);
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
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <form
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
        id="message-input"
        placeholder={placeholder}
        rows={2}
        value={value || ''}
        disabled={disabled}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
      <button
        type="button"
        id="message-attach"
        className="tooltip-above"
        data-tooltip="Attach images — paste a screenshot, drop a file, or click to pick."
        disabled={disabled}
        onClick={() => fileInputRef.current?.click()}
        aria-label="Attach images"
      >
        📎
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        multiple
        style={{ display: 'none' }}
        onChange={handleFilePickerChange}
      />
      <button
        type="submit"
        disabled={disabled || !hasContent}
        className={`${submitClass} tooltip-above`.trim()}
        data-tooltip={submitTitle}
      >
        {submitLabel}
      </button>
    </form>
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
