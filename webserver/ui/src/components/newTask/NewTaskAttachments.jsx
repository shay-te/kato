import { useRef, useState } from 'react';
import { toast } from '../../stores/toastStore.js';
import { humanSize } from '../../utils/textAttachment.js';
import { addAttachments, removeAttachmentAt } from './newTaskHelpers.js';

// Documents handed over while drafting — a spec, a CSV, a PDF. Creating the
// task IS its first prompt (the opener runs as soon as the clone is ready),
// so this is the one chance to attach them before the agent's first turn,
// not something patched in later over chat. Presentational only: ``files``
// and its persistence live in NewTaskPane, same split as NewTaskRepositories.
export default function NewTaskAttachments({ files, onChange }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  function ingest(picked) {
    const { files: next, rejected } = addAttachments(files, picked);
    onChange(next);
    if (rejected.length > 0) {
      toast.show({
        kind: 'warning',
        title: 'Some files were too large',
        message: `${rejected.join(', ')} — max 64 MB per file.`,
        durationMs: 6000,
      });
    }
  }

  function handlePick(event) {
    const picked = Array.from(event.target.files || []);
    event.target.value = '';
    if (picked.length) { ingest(picked); }
  }
  function hasFiles(event) {
    return Boolean(Array.from(event.dataTransfer?.types || []).includes('Files'));
  }
  function handleDragEnter(event) {
    if (!hasFiles(event)) { return; }
    event.preventDefault();
    setDragging(true);
  }
  function handleDragOver(event) {
    if (!hasFiles(event)) { return; }
    event.preventDefault();
  }
  function handleDrop(event) {
    event.preventDefault();
    setDragging(false);
    const picked = Array.from(event.dataTransfer?.files || []);
    if (picked.length) { ingest(picked); }
  }

  return (
    <section
      className={`new-task-section new-task-attachments${dragging ? ' is-drop-target' : ''}`}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <h4 className="new-task-section-title">
        Attachments
        <span className="new-task-repos-count">{files.length ? `${files.length} file(s)` : ''}</span>
      </h4>
      {files.length > 0 && (
        <ul className="new-task-attachments-list">
          {files.map((file, index) => (
            <li key={`${file.name}-${file.size}-${index}`} className="new-task-attachment">
              <span className="new-task-attachment-name" title={file.name}>{file.name}</span>
              <span className="new-task-attachment-size">{humanSize(file.size)}</span>
              <button
                type="button"
                className="new-task-attachment-remove"
                aria-label={`Remove ${file.name}`}
                title="Remove"
                onClick={() => onChange(removeAttachmentAt(files, index))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        id="new-task-attach"
        className="new-task-attach-btn"
        onClick={() => inputRef.current?.click()}
      >
        <span aria-hidden="true">+</span> Attach files…
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={handlePick}
        aria-label="Attach files"
      />
      <p className="new-task-attachments-hint">
        Documents the agent should read from the start — a spec, a CSV, a PDF. Drop files here or click to pick.
      </p>
    </section>
  );
}
