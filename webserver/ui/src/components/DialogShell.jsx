// Shared chrome for the ``.modal`` dialogs (PermissionModal and
// ForgetTaskModal), and — via ``inline`` — for the same content rendered as
// part of a page rather than over it.
//
// ``inline`` exists so the permission ask can live INSIDE the chat instead of
// as an overlay. An overlay is a demand for attention right now; the ask
// belongs to one conversation and should read as part of it, so the operator
// can scroll, read the transcript above it, and answer when ready. Same
// component either way — the content, the keyboard handling and the submit
// path are identical, and forking it would leave two of them to keep in step.
//
// Inline mode drops ``role="dialog"`` and ``aria-modal`` deliberately: a
// screen reader announcing a modal that traps nothing, over a page the user
// can still move around freely, is a lie about the interaction.
//
// Renders the
//
//   div.modal > div.modal-card > header.modal-head(h2 + subtitle) + {children}
//
// shape. Backdrop dismissal is opt-in because the two callers differ:
// ForgetTaskModal closes on a backdrop click; PermissionModal has no
// backdrop dismiss (the operator must make an explicit decision). Esc
// handling stays with the caller (ForgetTaskModal uses ``useEscapeKey``)
// since it depends on the caller's own mount lifecycle. The id
// attributes are passed through so existing selectors/tests keep
// working (#permission-modal-title, #forget-task-title, etc.).
export default function DialogShell({
  id,
  title,
  subtitle,
  subtitleId,
  ariaLabelledBy,
  onClose,
  backdropClose = false,
  inline = false,
  children,
}) {
  function handleBackdropClick(event) {
    if (event.target === event.currentTarget) {
      onClose();
    }
  }

  const card = (
    <div className="modal-card">
      <header className="modal-head">
        <h2 id={ariaLabelledBy}>{title}</h2>
        <span id={subtitleId}>{subtitle}</span>
      </header>
      {children}
    </div>
  );

  if (inline) {
    return (
      <section
        id={id}
        className="modal is-inline"
        aria-labelledby={ariaLabelledBy}
      >
        {card}
      </section>
    );
  }

  return (
    <div
      id={id}
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={ariaLabelledBy}
      onClick={backdropClose ? handleBackdropClick : undefined}
    >
      {card}
    </div>
  );
}
