import Icon from './Icon.jsx';
import {
  APPROVAL_MODE_GLOBAL,
  APPROVAL_MODE_IN_CHAT,
  writeApprovalMode,
} from '../utils/approvalModePref.js';

// Where approval requests show, switched FROM a request: a popup minimizes to
// the chat that asked, a request in the chat expands to a popup.
//
// This replaced the Settings option. The moment an operator cares where an
// ask is drawn is when one is in front of them — not in a settings drawer.
// The choice is global and sticks: every task's requests, from now on, until
// it is switched back the same way. The request on screen moves with it and
// keeps whatever was already typed into it (the answer form stores its draft
// under the request's id).
export default function ApprovalPlacementToggle({ inline = false }) {
  const next = inline ? APPROVAL_MODE_GLOBAL : APPROVAL_MODE_IN_CHAT;
  const icon = inline ? 'external-link' : 'minus';
  const label = inline ? 'Expand to a popup' : 'Minimize to the chat';
  const tooltip = inline
    ? 'Show approval requests as a popup over everything — for every task, from now on.'
    : 'Show approval requests inside the chat that asked — for every task, from now on. Another task’s request then lights its tab instead of interrupting.';
  return (
    <button
      type="button"
      className="approval-placement-toggle tooltip-end"
      data-tooltip={tooltip}
      aria-label={label}
      onClick={() => writeApprovalMode(next)}
    >
      <Icon name={icon} />
    </button>
  );
}
