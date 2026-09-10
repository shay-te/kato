import Icon from './Icon.jsx';
import MarkdownContent from './MarkdownContent.jsx';

// Centre-pane view for the agent's plan (``<workspace>/plan.md``).
// Presentational: App owns the plan poll + content (it also needs the
// content's ``mtime`` to decide when to auto-open this view), so this
// component just renders the markdown for review. Auto-opened when the
// agent presents a fresh plan via ExitPlanMode while in plan mode.

export default function PlanPane({ content, onClose }) {
  const text = String(content || '').trim();
  return (
    <section id="plan-pane">
      <header className="plan-pane-header">
        <span className="plan-pane-title">Plan</span>
        <span className="plan-pane-pill">review</span>
        {/* Every other centre-pane body can be dismissed — a file tab has
            its ✕, the orchestrator feed has one. The plan opens ITSELF (the
            watcher auto-shows a fresh plan), so it was the one view that
            took the centre column and would not give it back. */}
        {typeof onClose === 'function' && (
          <button
            type="button"
            className="plan-pane-close tooltip-end"
            onClick={onClose}
            data-tooltip="Close the plan and go back to the file preview. It stays in the workspace — reopen it any time."
            aria-label="Close plan"
          >
            <Icon name="xmark" />
          </button>
        )}
      </header>
      <div className="plan-pane-body">
        {text
          ? (
            <div className="plan-pane-markdown">
              <MarkdownContent>{text}</MarkdownContent>
            </div>
          )
          : (
            <div className="plan-pane-empty">
              <p>No plan yet.</p>
              <p className="plan-pane-empty-hint">
                When the agent presents a plan in plan mode, it appears here
                for review.
              </p>
            </div>
          )}
      </div>
    </section>
  );
}
