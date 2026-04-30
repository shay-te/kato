import { useEffect, useState } from 'react';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

export default function PermissionModal({ raw, onDecide }) {
  const { requestId, toolName, toolInput } = unpackPermissionEnvelope(raw);
  const [rationale, setRationale] = useState('');

  useEffect(() => { setRationale(''); }, [requestId]);

  if (!raw) { return null; }

  const fields = renderFields(toolInput);

  return (
    <div id="permission-modal" className="modal">
      <div className="modal-card">
        <header className="modal-head">
          <h2>Approval requested</h2>
          <span id="permission-tool-name">{toolName}</span>
        </header>
        <div id="permission-fields">{fields}</div>
        <details id="permission-raw" className="modal-raw">
          <summary>raw envelope</summary>
          <pre id="permission-detail">{safeStringify(raw)}</pre>
        </details>
        <textarea
          id="permission-rationale"
          placeholder="Optional rationale (sent if you Deny)"
          rows={2}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
        />
        <div className="modal-actions">
          <button
            id="permission-deny"
            type="button"
            className="danger"
            onClick={() => onDecide({
              allow: false, rationale, remember: false, requestId, toolName,
            })}
          >
            Deny
          </button>
          <button
            id="permission-allow-once"
            type="button"
            className="secondary"
            title={`Approve this ${toolName} request only — ask again next time`}
            onClick={() => onDecide({
              allow: true, rationale, remember: false, requestId, toolName,
            })}
          >
            Allow once
          </button>
          <button
            id="permission-allow-always"
            type="button"
            className="primary"
            title={`Approve and remember ${toolName} for the rest of this session`}
            onClick={() => onDecide({
              allow: true, rationale, remember: true, requestId, toolName,
            })}
          >
            Allow always
          </button>
        </div>
      </div>
    </div>
  );
}

function renderFields(toolInput) {
  if (!toolInput || typeof toolInput !== 'object'
      || Object.keys(toolInput).length === 0) {
    return (
      <p className="permission-field-value">(no arguments)</p>
    );
  }
  return Object.entries(toolInput).map(([key, value]) => (
    <div className="permission-field" key={key}>
      <span className="permission-field-label">{key}</span>
      <div className="permission-field-value">{formatValue(value)}</div>
    </div>
  ));
}

function formatValue(value) {
  if (value == null) { return ''; }
  if (typeof value === 'string') { return value; }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return safeStringify(value);
}

function safeStringify(value) {
  try { return JSON.stringify(value, null, 2); }
  catch (_) { return String(value); }
}
