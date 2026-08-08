/**
 * Remaining context-window indicator for the composer toolbar.
 *
 * Kato runs ONE Claude session per task and never restarts it behind the
 * operator's back — so when the window fills, the decision to `/compact` is
 * theirs, and they can only make it if they can see it coming. That is what
 * this is for.
 *
 * It reports REMAINING rather than used: "12% left" is the number you act on.
 *
 * When the window is unknown (no live session, or a model kato can't size) it
 * says so instead of rendering a percentage. A confident-looking bar built on
 * a guessed window would be worse than no bar — it would send someone into a
 * needless compaction, or let them hit the wall mid-task believing they had
 * room.
 */
export default function ContextMeter({ usage }) {
  const used = toCount(usage?.used_tokens);
  const limit = toCount(usage?.limit_tokens);
  const known = limit > 0 && used > 0;
  const remainingPct = known
    ? Math.max(0, Math.min(100, Math.round(((limit - used) / limit) * 100)))
    : null;
  const level = severity(remainingPct);

  return (
    <div
      className={`context-meter context-meter--${level}`}
      role="status"
      aria-label={
        known
          ? `Context window: ${remainingPct}% remaining, `
            + `${formatTokens(used)} of ${formatTokens(limit)} used`
          : 'Context window usage unknown'
      }
      data-tooltip={tooltip(known, used, limit, remainingPct)}
    >
      <span className="context-meter-track" aria-hidden="true">
        <span
          className="context-meter-fill"
          style={{ width: `${known ? 100 - remainingPct : 0}%` }}
        />
      </span>
      <span className="context-meter-label">
        {known ? `${remainingPct}% left` : 'context —'}
      </span>
    </div>
  );
}

// Warn early enough to act. Compacting takes a turn, so "act now" has to fire
// while there is still room to run it.
function severity(remainingPct) {
  if (remainingPct === null) { return 'unknown'; }
  if (remainingPct <= 10) { return 'critical'; }
  if (remainingPct <= 25) { return 'low'; }
  return 'ok';
}

function tooltip(known, used, limit, remainingPct) {
  if (!known) {
    return 'Context window usage is unknown until this task has a live '
      + 'Claude session that has completed a turn.';
  }
  const base = `Context window: ${formatTokens(used)} of ${formatTokens(limit)} `
    + `used, ${remainingPct}% left.`;
  if (remainingPct > 25) {
    return `${base} Kato keeps ONE session per task and never restarts it on `
      + 'its own — run /compact yourself when this gets low.';
  }
  return `${base} Run /compact from the commands menu to summarise the `
    + 'conversation and keep going in the same session.';
}

function toCount(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
}

function formatTokens(count) {
  if (count >= 1_000_000) {
    const millions = count / 1_000_000;
    return `${millions >= 10 ? Math.round(millions) : millions.toFixed(1)}M`;
  }
  if (count >= 1_000) { return `${Math.round(count / 1_000)}k`; }
  return String(count);
}
