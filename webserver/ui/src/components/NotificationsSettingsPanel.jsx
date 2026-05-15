import { NOTIFICATION_KIND } from '../constants/notificationKind.js';

// The body of the old NotificationSettings popover, extracted for
// reuse inside the SettingsDrawer's "Notifications" tab. Pure
// presentational — every callback is owned upstream by the
// useNotifications hook (toggle / per-kind enable) so this panel
// works the same regardless of where it's rendered.

const KIND_LABELS = {
  [NOTIFICATION_KIND.STARTED]: 'Task started',
  [NOTIFICATION_KIND.STATUS_CHANGE]: 'Task status changed',
  [NOTIFICATION_KIND.COMPLETED]: 'Task finished',
  [NOTIFICATION_KIND.ATTENTION]: 'Approval needed (chat / push)',
  [NOTIFICATION_KIND.ERROR]: 'Task failed / errored',
  [NOTIFICATION_KIND.REPLY]: 'Claude replied',
};

export default function NotificationsSettingsPanel({
  enabled,
  supported,
  permission,
  kindPrefs,
  onSetKindEnabled,
  onToggle,
}) {
  const masterLabel = enabled ? 'on' : 'off';
  const masterDisabled = !supported || permission === 'denied';
  const permissionHint = permission === 'denied' && (
    <div className="notification-settings-hint">
      Notifications are blocked at the browser level. Enable them
      in your browser site settings, then come back here.
    </div>
  );
  const unsupportedHint = !supported && (
    <div className="notification-settings-hint">
      This browser doesn't expose the Notifications API — toggles
      below are disabled.
    </div>
  );

  function makeKindHandler(kind) {
    return function onKindChange(event) {
      onSetKindEnabled(kind, event.target.checked);
    };
  }

  const kindRows = Object.values(NOTIFICATION_KIND).map((kind) => {
    const label = KIND_LABELS[kind] || kind;
    const checked = kindPrefs[kind] !== false;
    return (
      <label key={kind} className="notification-settings-row">
        <input
          type="checkbox"
          checked={checked}
          onChange={makeKindHandler(kind)}
          disabled={!enabled}
        />
        <span>{label}</span>
      </label>
    );
  });

  return (
    <div className="notifications-settings-panel">
      <label
        className="notification-settings-row notification-settings-master"
        title={enabled
          ? 'Turn off all browser notifications for kato.'
          : 'Turn on browser notifications so kato can ping you when a task needs you.'}
      >
        <span className="notification-settings-master-label">
          Browser notifications
          <span className="notification-settings-master-state">
            {masterLabel}
          </span>
        </span>
        <input
          type="checkbox"
          checked={enabled}
          onChange={onToggle}
          disabled={masterDisabled}
        />
      </label>
      {unsupportedHint}
      {permissionHint}
      <div className="notification-settings-divider" />
      <div className="notifications-settings-kinds-head">
        Choose which task events should ping you:
      </div>
      {kindRows}
    </div>
  );
}
