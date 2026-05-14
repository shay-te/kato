import Icon from './Icon.jsx';
import NotificationSettings from './NotificationSettings.jsx';

/**
 * Top app bar. Carries:
 *   - kato logo + name
 *   - live status line (used to be a separate StatusBar component;
 *     merged in so the operator's eyes don't have to bounce
 *     between two top rows for the same context)
 *   - bell / notification settings / refresh actions
 *
 * Status props (``statusLatest``, ``statusStale``, ``statusConnected``)
 * follow the same shape ``StatusBar`` used; rendering lives here now.
 */
export default function Header({
  notificationsEnabled,
  notificationsSupported,
  notificationsPermission,
  notificationKindPrefs,
  onSetKindEnabled,
  onToggleNotifications,
  onRefresh,
  statusLatest,
  statusStale = false,
  statusConnected = false,
}) {
  const bellTitle = notificationsEnabled
    ? 'Browser notifications: on (click to disable)'
    : 'Browser notifications: off (click to enable)';
  const bellIconName = notificationsEnabled ? 'bell' : 'bell-slash';

  const level = String(statusLatest?.level || 'INFO').toUpperCase();
  const statusKind = level === 'ERROR'
    ? 'is-error'
    : (level === 'WARNING' || level === 'WARN' ? 'is-warn' : '');
  const statusClassName = [
    'header-status',
    statusKind,
    statusStale ? 'is-stale' : '',
  ].filter(Boolean).join(' ');

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
    <header>
      <img src="/logo.png" alt="Kato" id="kato-logo" />
      <h1>Kato</h1>
      <span className="subtitle">Planning UI</span>
      <span className={statusClassName} title="Live kato activity">
        <span className="header-status-pulse" aria-hidden="true" />
        <span className="header-status-text">{statusText}</span>
      </span>
      <button
        type="button"
        data-tooltip={bellTitle}
        aria-label={bellTitle}
        onClick={onToggleNotifications}
        disabled={!notificationsSupported}
      >
        <Icon name={bellIconName} />
      </button>
      <NotificationSettings
        enabled={notificationsEnabled}
        supported={notificationsSupported}
        permission={notificationsPermission}
        kindPrefs={notificationKindPrefs || {}}
        onSetKindEnabled={onSetKindEnabled}
        onToggle={onToggleNotifications}
      />
      <button
        type="button"
        data-tooltip="Refresh the task list — re-scans tickets and reloads workspace state."
        aria-label="Refresh sessions"
        onClick={onRefresh}
      >
        <Icon name="refresh" />
      </button>
    </header>
  );
}
