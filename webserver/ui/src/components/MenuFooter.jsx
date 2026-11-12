import Icon from './Icon.jsx';
import LidAwakeChip from './LidAwakeChip.jsx';
import ShutdownButton from './ShutdownButton.jsx';
import { cx } from '../utils/cx.js';

// Where the hover labels of the footer's controls open: ABOVE (the footer is
// the bottom of the screen) and from the control's LEFT edge (the rail is the
// screen's left edge — a centred label would run off it).
export const FOOTER_TOOLTIP = 'tooltip-above tooltip-anchor-left';

// The bottom of the tasks rail: what the app bar used to carry, now that there
// is none. Top to bottom:
//   - the tasks waiting for your answer (``#menu-attention-slot``, filled by
//     GlobalPermissionContainer — it owns that list; a slot, not props),
//   - kato's live status line (a click opens the activity feed),
//   - whether closing the lid stops kato,
//   - usage, release notes, settings, shut down. (No refresh here: the rail's
//     Scan now is the one refresh — a second one under the same icon did less
//     and read as the same thing.)
//
// Laid out the same collapsed and expanded: expanding the rail must not move
// a control out from under the pointer that is about to press it.
export default function MenuFooter({
  statusLatest,
  statusStale = false,
  statusConnected = false,
  onStatusClick,
  statusActive = false,
  onOpenSettings,
  onOpenUsage,
  onOpenReleaseNotes,
  lidAwake = null,
}) {
  const level = String(statusLatest?.level || 'INFO').toUpperCase();
  const statusKind = level === 'ERROR'
    ? 'is-error'
    : (level === 'WARNING' || level === 'WARN' ? 'is-warn' : '');
  const statusClickable = typeof onStatusClick === 'function';
  const statusClassName = cx(
    'header-status',
    statusKind,
    statusStale ? 'is-stale' : '',
    statusClickable ? 'is-clickable' : '',
    statusActive ? 'is-active' : '',
  );

  let statusText;
  if (statusLatest?.message) {
    statusText = statusLatest.message;
  } else if (statusStale) {
    statusText = 'Lost connection to kato. Retrying…';
  } else if (statusConnected) {
    statusText = 'Connected to kato — waiting for the next scan tick.';
  } else {
    statusText = 'Connecting to kato…';
  }
  const status = (
    <>
      <span className="header-status-pulse" aria-hidden="true" />
      <span className="header-status-text">{statusText}</span>
    </>
  );

  return (
    <div className="task-menu-footer">
      {/* Always rendered, so the portal target is stable. */}
      <span id="menu-attention-slot" className="menu-attention-slot" />
      {statusClickable ? (
        <button
          type="button"
          className={statusClassName}
          onClick={onStatusClick}
          aria-pressed={statusActive ? 'true' : 'false'}
          // The whole line: the rail shows only its start.
          title={`${statusText}\n\n${statusActive
            ? 'Click to hide the orchestrator activity feed and bring the editor back.'
            : 'Click to open the live orchestrator activity feed in the centre pane.'}`}
        >
          {status}
        </button>
      ) : (
        <span className={statusClassName} title={statusText}>{status}</span>
      )}
      <LidAwakeChip status={lidAwake} tooltipClassName={FOOTER_TOOLTIP} />
      <div className="task-menu-footer-icons">
        {/* ``header-icon-btn`` is an OPT-IN class: it styles what asks for
            it and nothing else (see HeaderButtonBaseCss.test.js). */}
        <button
          type="button"
          className={cx('header-icon-btn', FOOTER_TOOLTIP)}
          data-tooltip="Usage — how many tokens each task has consumed."
          aria-label="Open usage"
          onClick={onOpenUsage}
          disabled={typeof onOpenUsage !== 'function'}
        >
          <Icon name="chart" />
        </button>
        <button
          type="button"
          className={cx('header-icon-btn', FOOTER_TOOLTIP)}
          data-tooltip="Release notes — a dated line for every task that reached Done."
          aria-label="Open release notes"
          onClick={onOpenReleaseNotes}
          disabled={typeof onOpenReleaseNotes !== 'function'}
        >
          <Icon name="notes" />
        </button>
        <button
          type="button"
          className={cx('header-icon-btn', FOOTER_TOOLTIP)}
          data-tooltip="Settings — repositories, providers, notifications, and more."
          aria-label="Open settings"
          onClick={onOpenSettings}
          disabled={typeof onOpenSettings !== 'function'}
        >
          <Icon name="gear" />
        </button>
        <ShutdownButton tooltipClassName={FOOTER_TOOLTIP} />
      </div>
    </div>
  );
}
