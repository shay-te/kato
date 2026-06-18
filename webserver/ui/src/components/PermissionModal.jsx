import { useEffect, useState } from 'react';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
  isHighRiskActionGuard,
} from '../utils/permissionEnvelope.js';
import DialogShell from './DialogShell.jsx';
import AskUserQuestionForm from './AskUserQuestionForm.jsx';

export default function PermissionModal({
  raw, onDecide, taskCode = '', taskSummary = '',
}) {
  const {
    taskId, taskSummary: envelopeSummary,
    requestId, toolName, toolInput, outsideSandbox, outsidePath, actionGuard,
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
  const actionGuardBanner = renderActionGuardBanner(actionGuard);
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
  // Out-of-task asks AND high-risk Action Guard categories (credential read,
  // exfil, remote-exec, sandbox escape) never offer the remembered ("Allow
  // always") scope — a persisted grant for those is exactly what must never
  // be one click away. Built here so the JSX stays logic-free.
  const withholdAllowAlways = outsideSandbox || isHighRiskActionGuard(actionGuard);
  const allowAlwaysButton = renderAllowAlwaysButton(
    withholdAllowAlways, allowAlwaysTitle, handleAllowAlways,
  );

  // Always name the task in the title so the operator knows WHICH task is
  // waiting — not just "approval requested". ``taskCode`` comes straight from
  // the rendering surface (the focused task's SSE envelope has no task_id);
  // the cross-task feed also stamps ``task_id`` + ``task_summary`` on the
  // envelope itself. The summary prop wins over the envelope's copy (the
  // focused-task path has the freshest value).
  const code = taskCode || taskId;
  const summary = String(taskSummary || envelopeSummary || '').trim();
  // Two-line title — both lines styled identically (same font / size /
  // colour / weight). Line 1: "<TASK-CODE> — <summary>" (or "Approval
  // requested" when the task is unknown — the old fallback). Line 2:
  // "wants permission <ToolName>". The tool line ALWAYS renders so the
  // operator never loses the tool name on the no-task-code path.
  const title = (
    <span className="permission-modal-title-stack">
      <span className="permission-modal-title-line">
        {code ? (
          <>
            <span className="permission-modal-task">{code}</span>
            {summary && (
              <>
                <span className="permission-modal-title-sep"> — </span>
                <span className="permission-modal-title-summary">{summary}</span>
              </>
            )}
          </>
        ) : (
          'Approval requested'
        )}
      </span>
      <span className="permission-modal-title-line">
        wants permission <span className="permission-modal-tool">{toolName}</span>
      </span>
    </span>
  );

  // AskUserQuestion isn't a permission to grant — it's a question to answer.
  // Render the options as a real answer form; the selection goes back as the
  // response message (a "deny" carrying the answer, since an "allow" would make
  // the headless CLI try to open a TTY picker that doesn't exist).
  const askQuestions = toolName === 'AskUserQuestion'
    && Array.isArray(toolInput?.questions) && toolInput.questions.length > 0
    ? toolInput.questions
    : null;
  if (askQuestions) {
    return (
      <DialogShell
        id="permission-modal"
        ariaLabelledBy="permission-modal-title"
        title={title}
      >
        <AskUserQuestionForm
          questions={askQuestions}
          onAnswer={(answerText) => onDecide({
            allow: false, rationale: answerText, remember: false,
            requestId, toolName, command,
          })}
          onDismiss={() => onDecide({
            allow: false,
            rationale: 'The user dismissed the question without answering.',
            remember: false, requestId, toolName, command,
          })}
        />
      </DialogShell>
    );
  }

  return (
    <DialogShell
      id="permission-modal"
      ariaLabelledBy="permission-modal-title"
      title={title}
    >
      {sandboxWarning}
      {actionGuardBanner}
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

// The Action Guard risk banner — names the risk category + reason so the
// operator decides with context. This modal only ever appears for an ASK (a
// BLOCK is auto-denied and shown as a feed bubble, never here), so it's an
// amber "please confirm" — NOT the red the refusal/sandbox warning uses. A red
// "DESTRUCTIVE FS" on an in-scope `rm -rf tests/test_data` read as a
// catastrophe when it's just a dual-use action the operator is approving.
// Null when the ask carries no classification. Built outside JSX.
function renderActionGuardBanner(actionGuard) {
  if (!actionGuard || !actionGuard.category) { return null; }
  const category = String(actionGuard.category || '').replace(/_/g, ' ').toUpperCase();
  const reason = String(actionGuard.reason || '');
  return (
    <div id="permission-action-guard" className="permission-action-guard-banner" role="alert">
      <h1 className="permission-sandbox-warning-title">
        ⚠ ACTION GUARD — {category}
      </h1>
      {reason && <p className="permission-sandbox-warning-body">{reason}</p>}
    </div>
  );
}

// The remembered-scope button — withheld (null) for out-of-task asks and
// high-risk Action Guard categories.
function renderAllowAlwaysButton(withhold, allowAlwaysTitle, onAllowAlways) {
  if (withhold) { return null; }
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
