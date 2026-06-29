import MarkdownContent from './MarkdownContent.jsx';

// Centre-pane view for the agent's plan (``<workspace>/plan.md``).
// Presentational: App owns the plan poll + content (it also needs the
// content's ``mtime`` to decide when to auto-open this view), so this
// component just renders the markdown for review. Auto-opened when the
// agent presents a fresh plan via ExitPlanMode while in plan mode.

export default function PlanPane({ content }) {
  const text = String(content || '').trim();
  return (
    <section id="plan-pane">
      <header className="plan-pane-header">
        <span className="plan-pane-title">Plan</span>
        <span className="plan-pane-pill">review</span>
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
