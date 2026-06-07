import { useEffect, useState } from 'react';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
} from '../utils/permissionEnvelope.js';
import DialogShell from './DialogShell.jsx';

export default function PermissionModal({ raw, onDecide }) {
  const {
    taskId, requestId, toolName, toolInput, outsideSandbox, outsidePath,
  } = unpackPermissionEnvelope(raw);
  const [rationale, setRationale] = useState('');

  useEffect(() => { setRationale(''); }, [requestId]);

  if (!raw) { return null; }

  // Command-keyed tools (Bash) remember the command's PROGRAM (e.g. `mvn`),
  // not the tool and not the verbatim line — so "Allow always" on
  // `mvn verify` covers future `mvn` runs (in any task folder) but never
  // auto-allows `docker`. The modal still shows the real command below.
  const command = decisionCommandFor(toolName, toolInput);

  // Out-of-task asks (a file path or an escaping command like docker —
  // flagged by the backend's outside_sandbox) get the loud red warning and
  // never offer a remembered ("Allow always") scope. Ordinary in-task
  // commands use the normal flow.
  const fields = renderFields(toolInput);
  const sandboxWarning = renderSandboxWarning(outsideSandbox, outsidePath, toolName);
  const denyTooltip = `Deny this ${toolName} request. Claude will see your rationale (if any) and decide what to do next.`;
  const allowOnceTitle = `Approve this ${toolName} request only — kato will ask again next time.`;
  const allowAlwaysTitle = `Approve and remember ${toolName} — kato won't ask again, even after a kato or browser restart, until you clear it from settings.`;
  function handleRationaleChange(event) {
    setRationale(event.target.value);
  }
  function handleDeny() {
    onDecide({ allow: false, rationale, remember: false, requestId, toolName, command });
  }
  function handleAllowOnce() {
    onDecide({ allow: true, rationale, remember: false, requestId, toolName, command });
  }
  function handleAllowAlways() {
    onDecide({ allow: true, rationale, remember: true, requestId, toolName, command });
  }
  // Out-of-task asks never offer the remembered ("Allow always") scope —
  // built here, before the return, so the JSX stays logic-free.
  const allowAlwaysButton = renderAllowAlwaysButton(
    outsideSandbox, allowAlwaysTitle, handleAllowAlways,
  );

  // When the ask is for a task other than the one in focus (the global
  // cross-task feed stamps ``task_id``), name it in the title so the operator
  // knows WHICH task is waiting — not just "approval requested".
  const title = taskId
    ? `${taskId} wants permission`
    : 'Approval requested';

  return (
    <DialogShell
      id="permission-modal"
      ariaLabelledBy="permission-modal-title"
      title={title}
      subtitle={toolName}
      subtitleId="permission-tool-name"
    >
      {sandboxWarning}
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
        onChange={handleRationaleChange}
      />
      <div className="modal-actions">
        <button
          id="permission-deny"
          type="button"
          className="danger tooltip-above"
          data-tooltip={denyTooltip}
          onClick={handleDeny}
        >
          Deny
        </button>
        <button
          id="permission-allow-once"
          type="button"
          className="secondary tooltip-above"
          data-tooltip={allowOnceTitle}
          onClick={handleAllowOnce}
        >
          Allow once
        </button>
        {allowAlwaysButton}
      </div>
    </DialogShell>
  );
}

// The loud out-of-sandbox alarm: an <h1> headline, the offending path on
// its OWN line, then the single-action guidance. Returns null when the
// ask is in-sandbox. Built outside the JSX return so the render stays
// logic-free (AGENTS.md "no logic inside JSX").
function renderSandboxWarning(outsideSandbox, outsidePath, toolName) {
  if (!outsideSandbox) { return null; }
  const pathLine = outsidePath
    ? <code className="permission-sandbox-warning-path">{outsidePath}</code>
    : null;
  return (
    <div id="permission-outside-sandbox" className="permission-sandbox-warning" role="alert">
      <h1 className="permission-sandbox-warning-title">
        ⚠ CLAUDE IS REACHING OUTSIDE THE TASK FOLDER
      </h1>
      {pathLine}
      <p className="permission-sandbox-warning-body">
        Approve this {toolName} <strong>only for this single action</strong>.
        A remembered approval is not offered for out-of-sandbox access.
      </p>
    </div>
  );
}

// The remembered-scope button — withheld (null) for out-of-task asks.
function renderAllowAlwaysButton(outsideSandbox, allowAlwaysTitle, onAllowAlways) {
  if (outsideSandbox) { return null; }
  return (
    <button
      id="permission-allow-always"
      type="button"
      className="primary tooltip-above"
      data-tooltip={allowAlwaysTitle}
      onClick={onAllowAlways}
    >
      Allow always
    </button>
  );
}

function renderFields(toolInput) {
  const isEmpty = !toolInput
    || typeof toolInput !== 'object'
    || Object.keys(toolInput).length === 0;
  if (isEmpty) {
    return (
      <p className="permission-field-value">(no arguments)</p>
    );
  }
  return Object.entries(toolInput).map(([key, value]) => {
    const formatted = formatValue(value);
    return (
      <div className="permission-field" key={key}>
        <span className="permission-field-label">{key}</span>
        <div className="permission-field-value">{formatted}</div>
      </div>
    );
  });
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
