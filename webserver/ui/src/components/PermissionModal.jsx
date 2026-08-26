import { useEffect, useState } from 'react';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
  isHighRiskActionGuard,
  NEVER_REMEMBERED_TOOLS,
} from '../utils/permissionEnvelope.js';
import { extractAnswerableQuestions } from '../utils/answerableQuestion.js';
import { backendLabel } from './AgentBackendChip.jsx';
import DialogShell from './DialogShell.jsx';
import AskUserQuestionForm from './AskUserQuestionForm.jsx';
import MarkdownContent from './MarkdownContent.jsx';

export default function PermissionModal({
  raw, onDecide, taskCode = '', taskSummary = '', queuedCount = 0,
}) {
  const {
    taskId, taskSummary: envelopeSummary, agentBackend,
    requestId, toolName, toolInput, outsideSandbox, outsidePath, actionGuard,
  } = unpackPermissionEnvelope(raw);
  // Falls back to the neutral word ONLY when the ask carries no backend (an
  // older server). Naming a specific agent on a guess would be worse than
  // saying "the agent" — the operator is about to authorise a command.
  const agentName = backendLabel(agentBackend) || 'The agent';
  const [rationale, setRationale] = useState('');

  useEffect(() => { setRationale(''); }, [requestId]);

  // Command-keyed tools (Bash) remember the command's PROGRAM (e.g. `mvn`),
  // not the tool and not the verbatim line — so "Allow always" on
  // `mvn verify` covers future `mvn` runs (in any task folder) but never
  // auto-allows `docker`. The modal still shows the real command below.
  const command = decisionCommandFor(toolName, toolInput);
  // Detected up here (not just before its render branch) so the keyboard
  // shortcuts below can skip the AskUserQuestion form, which has its own
  // controls. See the ``if (askQuestions)`` branch further down.
  const askQuestions = extractAnswerableQuestions(toolInput);
  // Out-of-task asks, high-risk Action Guard categories, AND permission-
  // changing tools never offer the remembered ("Allow always") scope — a
  // persisted grant for those is exactly what must never be one click (or
  // keystroke) away.
  //
  // ``ExitPlanMode`` is in that last group because it is not one action, it
  // is the whole of plan mode's enforcement. The grant would be stored under
  // the bare tool name, so it applies to EVERY task and survives restarts:
  // one click here and no plan-locked session ever asks again. The backend
  // refuses to auto-resolve it too (_NEVER_AUTO_RESOLVED_TOOLS); this hides
  // the button so the operator is never offered a choice that would not be
  // honoured.
  const withholdAllowAlways = (
    outsideSandbox
    || isHighRiskActionGuard(actionGuard)
    || NEVER_REMEMBERED_TOOLS.has(toolName)
  );

  // Keyboard shortcuts: Esc = Deny, Enter = Allow once, Shift+Enter = Allow
  // always (falls back to Allow once when the remembered scope is withheld).
  //
  // NEVER claimed while the operator is typing somewhere else. The popup
  // appears over whatever they are writing without moving focus, so their
  // next Enter — meant to send the message they were halfway through — used
  // to approve a request they had not read AND submit the half-written
  // prompt. Two decisions from one keystroke, neither intended. The
  // rationale box is excluded for the same reason (Enter is a newline
  // there), and so is the AskUserQuestion form, which has its own controls.
  //
  // ``stopImmediatePropagation`` as well as ``preventDefault``: the latter
  // only cancels the browser's default action, so without it the SAME
  // keydown still reached the composer's own handler and sent the draft.
  // That was the second half of the double-fire.
  useEffect(() => {
    if (!raw || askQuestions) { return undefined; }
    function onKeyDown(event) {
      const target = event.target || document.activeElement;
      const tag = target && target.tagName;
      const inTextField = (
        tag === 'INPUT' || tag === 'TEXTAREA' || !!(target && target.isContentEditable)
      );
      if (inTextField) { return; }
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        onDecide({ allow: false, rationale, remember: false, requestId, toolName, command });
      } else if (event.key === 'Enter') {
        event.preventDefault();
        event.stopImmediatePropagation();
        onDecide({
          allow: true, rationale, remember: event.shiftKey && !withholdAllowAlways,
          requestId, toolName, command,
        });
      }
    }
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [raw, askQuestions, withholdAllowAlways, rationale, requestId, toolName, command, onDecide]);

  if (!raw) { return null; }

  // Out-of-task asks (a file path or an escaping command like docker —
  // flagged by the backend's outside_sandbox) get the loud red warning and
  // never offer a remembered ("Allow always") scope. Ordinary in-task
  // commands use the normal flow.
  const fields = renderFields(toolInput);
  const sandboxWarning = renderSandboxWarning(
    outsideSandbox, outsidePath, toolName, agentName,
  );
  const actionGuardBanner = renderActionGuardBanner(actionGuard);
  const denyTooltip = `Deny this ${toolName} request (Esc). ${agentName} will see your rationale (if any) and decide what to do next.`;
  const allowOnceTitle = `Approve this ${toolName} request only (Enter) — kato will ask again next time.`;
  const allowAlwaysTitle = `Approve and remember ${toolName} (Shift+Enter) — kato won't ask again, even after a kato or browser restart, until you clear it from settings.`;
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
  // Other asks queue behind this dialog instead of replacing it (see
  // GlobalPermissionContainer), so say they are there — an unseen ask is an
  // agent blocked for as long as nobody notices it.
  const queuedNote = queuedCount > 0
    ? `${queuedCount} more request${queuedCount > 1 ? 's' : ''} waiting — `
      + 'you\'ll see them after this one'
    : '';
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
        <span className="permission-modal-agent">{agentName}</span>
        {' '}wants permission{' '}
        <span className="permission-modal-tool">{toolName}</span>
      </span>
    </span>
  );

  // A "question to answer" tool call (Claude's AskUserQuestion, or any backend
  // emitting the same questions shape) isn't a permission to grant — it's a
  // question to answer (detected as ``askQuestions`` above). Render the options
  // as a real answer form; the selection goes back as the response message (a
  // "deny" carrying the answer, since an "allow" would make the headless CLI
  // try to open a TTY picker that doesn't exist).
  if (askQuestions) {
    return (
      <DialogShell
        id="permission-modal"
        ariaLabelledBy="permission-modal-title"
        title={title}
        subtitle={queuedNote}
        subtitleId="permission-queued-note"
      >
        <AskUserQuestionForm
          // Keyed by the ask so a NEW question always starts blank, and its
          // partial answer is stored under that same id (see the form).
          key={requestId}
          draftKey={requestId}
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
      subtitle={queuedNote}
      subtitleId="permission-queued-note"
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
function renderSandboxWarning(outsideSandbox, outsidePath, toolName, agentName) {
  if (!outsideSandbox) { return null; }
  const pathLine = outsidePath
    ? <code className="permission-sandbox-warning-path">{outsidePath}</code>
    : null;
  return (
    <div id="permission-outside-sandbox" className="permission-sandbox-warning" role="alert">
      <h1 className="permission-sandbox-warning-title">
        ⚠ {agentName.toUpperCase()} IS REACHING OUTSIDE THE TASK FOLDER
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
    // The ExitPlanMode ``plan`` field is a markdown document — render it as
    // markdown (headings/lists/code), not the raw ``#``/``-`` text.
    if (key === 'plan' && typeof value === 'string' && value.trim()) {
      return (
        <div className="permission-field" key={key}>
          <span className="permission-field-label">{key}</span>
          <div className="permission-field-value permission-field-markdown">
            <MarkdownContent>{value}</MarkdownContent>
          </div>
        </div>
      );
    }
    return (
      <div className="permission-field" key={key}>
        <span className="permission-field-label">{key}</span>
        <div className="permission-field-value">{formatValue(value)}</div>
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
