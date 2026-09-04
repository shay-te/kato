import NotificationPrefsBody from './NotificationPrefsBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// The body of the old NotificationSettings popover, extracted for
// reuse inside the SettingsDrawer's "Notifications" tab. Pure
// presentational — every callback is owned upstream by the
// useNotifications hook (toggle / per-kind enable) so this panel
// works the same regardless of where it's rendered. The actual
// controls live in the shared <NotificationPrefsBody> (variant="panel").
export default function NotificationsSettingsPanel(props) {
  return (
    <div className="settings-drawer-panel notifications-settings-panel">
      {/* Every other tab opens with a SettingsPanelHead; this one was the
        * lone exception, so the Notifications tab started straight on a
        * toggle with nothing naming the page. */}
      <SettingsPanelHead title="Notifications">
        <p>
          How kato gets your attention when a task needs you — a desktop
          notification, a sound, or both. Stored per browser.
        </p>
      </SettingsPanelHead>
      <NotificationPrefsBody variant="panel" {...props} />
    </div>
  );
}
