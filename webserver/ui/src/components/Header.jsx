import { useLayoutEffect, useRef } from 'react';
import Icon, { BusyIcon } from './Icon.jsx';
import LidAwakeChip from './LidAwakeChip.jsx';
import ShutdownButton from './ShutdownButton.jsx';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { cx } from '../utils/cx.js';
import {
  closeTaskMenuSoon, openTaskMenu, useTaskMenuOpen,
} from '../stores/taskMenuStore.js';

/**
 * Top app bar. Carries:
 *   - kato logo + name
 *   - live status line (used to be a separate StatusBar component;
 *     merged in so the operator's eyes don't have to bounce
 *     between two top rows for the same context)
 *   - settings + refresh actions
 *
 * Notification enable/disable used to be a standalone bell button
 * here; it now lives in the Settings drawer's Notifications tab
 * (master toggle + per-kind toggles), so the header stays uncluttered.
 *
 * Status props (``statusLatest``, ``statusStale``, ``statusConnected``)
 * follow the same shape ``StatusBar`` used; rendering lives here now.
 */
export default function Header({
  onRefresh,
  statusLatest,
  statusStale = false,
  statusConnected = false,
  onStatusClick,
  statusActive = false,
  onOpenSettings,
  onOpenUsage,
  onOpenReleaseNotes,
  lidAwake = null,
  // How many tasks wait for the operator. The tasks menu is closed most of
  // the time, so the icon that opens it carries this — a task asking for
  // you must not hide behind a hover.
  attentionCount = 0,
}) {
  const menuOpen = useTaskMenuOpen();
  // The tasks menu hangs from the bottom of this bar, so it reads the bar's
  // real height (``--app-header-height``) instead of a number that drifts
  // the next time the bar changes.
  const headerRef = useRef(null);
  useLayoutEffect(() => {
    const node = headerRef.current;
    if (!node) { return undefined; }
    const publish = () => {
      const height = Math.round(node.getBoundingClientRect().height);
      if (height > 0) {
        document.documentElement.style.setProperty('--app-header-height', `${height}px`);
      }
    };
    publish();
    if (typeof ResizeObserver !== 'function') { return undefined; }
    const observer = new ResizeObserver(publish);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  // Refresh re-scans tickets + reloads workspace state — show the thick
  // spinner on the button while that's in flight (and block a double-tap).
  const [refreshing, runRefresh] = useBusyAction(onRefresh, {
    enabled: typeof onRefresh === 'function',
  });
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

  return (
    <header ref={headerRef}>
      {/* The tasks menu's handle (Chrome's vertical tabs, from the corner):
          hover opens it, leaving it and the menu closes it. A click opens it
          too — a touch screen has nothing to hover with. */}
      <button
        type="button"
        id="kato-menu-trigger"
        className={cx('kato-menu-trigger', menuOpen && 'is-open')}
        aria-label={attentionCount
          ? `Tasks — ${attentionCount} waiting for you`
          : 'Tasks'}
        aria-haspopup="true"
        aria-expanded={menuOpen ? 'true' : 'false'}
        aria-controls="tabs-pane"
        onMouseEnter={openTaskMenu}
        onMouseLeave={closeTaskMenuSoon}
        onClick={openTaskMenu}
      >
        <img src="/logo.png" alt="" id="kato-logo" />
        {/* The logo alone did not read as a menu: the bars say it is one. */}
        <span className="kato-menu-glyph" aria-hidden="true"><Icon name="menu" /></span>
        {attentionCount > 0 ? (
          <span className="kato-menu-badge" aria-hidden="true">{attentionCount}</span>
        ) : null}
      </button>
      <h1>Kato</h1>
      <span className="subtitle">Planning UI</span>
      {/* Where "a task needs you" lands, filled by GlobalPermissionContainer.
          A slot rather than props: that component already owns the pending
          list (its own poll plus the live SSE push), and threading the same
          data down here would be a second copy to keep in step. Always
          rendered so the portal target is stable. */}
      <span id="header-attention-slot" className="header-attention-slot" />
      {statusClickable ? (
        <button
          type="button"
          className={statusClassName}
          onClick={onStatusClick}
          aria-pressed={statusActive ? 'true' : 'false'}
          title={statusActive
            ? 'Hide the orchestrator activity feed and bring the editor back.'
            : 'Click to open the live orchestrator activity feed in the centre pane.'}
        >
          <span className="header-status-pulse" aria-hidden="true" />
          <span className="header-status-text">{statusText}</span>
        </button>
      ) : (
        <span className={statusClassName} title="Live kato activity">
          <span className="header-status-pulse" aria-hidden="true" />
          <span className="header-status-text">{statusText}</span>
        </span>
      )}
      <LidAwakeChip status={lidAwake} />
      {/* ``header-icon-btn`` is an OPT-IN, and these two are the entire reason
          the style exists. It used to be a descendant rule — ``header
          button:not(.header-status)`` — which reached every button inside
          every <header> in the app and forced an icon box on things that are
          not icons (the waiting-task chip is portaled in here, so no reading
          of this file could have shown that). A class only reaches what asks
          for it. */}
      <button
        type="button"
        className="header-icon-btn"
        data-tooltip="Usage — how many tokens each task has consumed."
        aria-label="Open usage"
        onClick={onOpenUsage}
        disabled={typeof onOpenUsage !== 'function'}
      >
        <Icon name="chart" />
      </button>
      <button
        type="button"
        className="header-icon-btn"
        data-tooltip="Release notes — a dated line for every task that reached Done."
        aria-label="Open release notes"
        onClick={onOpenReleaseNotes}
        disabled={typeof onOpenReleaseNotes !== 'function'}
      >
        <Icon name="notes" />
      </button>
      <button
        type="button"
        className="header-icon-btn"
        data-tooltip="Settings — repositories, providers, notifications, and more."
        aria-label="Open settings"
        onClick={onOpenSettings}
        disabled={typeof onOpenSettings !== 'function'}
      >
        <Icon name="gear" />
      </button>
      <button
        type="button"
        className="header-icon-btn"
        data-tooltip="Refresh the task list — re-scans tickets and reloads workspace state."
        aria-label={refreshing ? 'Refreshing…' : 'Refresh sessions'}
        aria-busy={refreshing}
        disabled={refreshing || typeof onRefresh !== 'function'}
        onClick={runRefresh}
      >
        <BusyIcon busy={refreshing} idle="refresh" />
      </button>
      <ShutdownButton />
    </header>
  );
}
