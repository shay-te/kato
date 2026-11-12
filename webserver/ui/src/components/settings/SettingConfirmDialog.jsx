import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import DialogShell from '../DialogShell.jsx';
import ModalFooterActions from '../ModalFooterActions.jsx';

// The "are you sure?" a setting asks before taking a value — the schema's
// ``confirm`` on the field ({when, title, lead, effects, confirm_label}).
// Strict git asks it on the way OFF: the agent may then rewrite its branch's
// history, and the operator should read what that changes before it does.
//
// Portalled to <body>: the Settings drawer slides in with a CSS transform,
// and a ``position: fixed`` child of a transformed element is placed against
// the drawer, not the window — the popup would sit squeezed inside the drawer.
//
// Escape cancels THIS popup only. The drawer closes on Escape too (a window
// listener), so this one listens in the capture phase and stops the event:
// one keypress, one thing closed.
export default function SettingConfirmDialog({ field, onConfirm, onCancel }) {
  const confirm = field.confirm || {};

  // Cancel has the focus: Enter on an unread popup must not change a setting.
  useEffect(() => {
    document.querySelector('#setting-confirm-modal .adopt-session-cancel')?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Escape') { return; }
      event.preventDefault();
      event.stopImmediatePropagation();
      onCancel();
    }
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [onCancel]);

  const effects = Array.isArray(confirm.effects) ? confirm.effects : [];
  return createPortal(
    <DialogShell
      id="setting-confirm-modal"
      ariaLabelledBy="setting-confirm-title"
      title={confirm.title || `Change ${field.label}?`}
      subtitle={field.label}
      subtitleId="setting-confirm-field"
      onClose={onCancel}
      backdropClose
    >
      {confirm.lead && <p className="setting-confirm-lead">{confirm.lead}</p>}
      {effects.length > 0 && (
        <ul className="setting-confirm-effects">
          {effects.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      <ModalFooterActions
        onCancel={onCancel}
        onConfirm={onConfirm}
        canConfirm
        confirmLabel={confirm.confirm_label || 'Change it'}
      />
    </DialogShell>,
    document.body,
  );
}
