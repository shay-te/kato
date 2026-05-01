import Icon from './Icon.jsx';
import NotificationSettings from './NotificationSettings.jsx';

export default function Header({
  notificationsEnabled,
  notificationsSupported,
  notificationsPermission,
  notificationKindPrefs,
  onSetKindEnabled,
  onToggleNotifications,
  onRefresh,
}) {
  const bellTitle = notificationsEnabled
    ? 'Browser notifications: on (click to disable)'
    : 'Browser notifications: off (click to enable)';
  const bellIconName = notificationsEnabled ? 'bell' : 'bell-slash';
  return (
    <header>
      <img src="/logo.png" alt="Kato" id="kato-logo" />
      <h1>Kato</h1>
      <span className="subtitle">Planning UI</span>
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
