import { useEffect, useRef, useState } from 'react';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import Icon from './Icon.jsx';

const KIND_LABELS = {
  [NOTIFICATION_KIND.STARTED]: 'Task started',
  [NOTIFICATION_KIND.STATUS_CHANGE]: 'Task status changed',
  [NOTIFICATION_KIND.COMPLETED]: 'Task finished',
  [NOTIFICATION_KIND.ATTENTION]: 'Approval needed (chat / push)',
  [NOTIFICATION_KIND.ERROR]: 'Task failed / errored',
  [NOTIFICATION_KIND.REPLY]: 'Claude replied',
};

export default function NotificationSettings({
  enabled,
  supported,
  permission,
  kindPrefs,
  onSetKindEnabled,
  onToggle,
}) {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef(null);

  useEffect(() => {
    if (!open) { return; }
    function onClickOutside(event) {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  const masterLabel = enabled ? 'on' : 'off';
  const masterDisabled = !supported || permission === 'denied';
  const permissionHint = permission === 'denied' && (
    <div className="notification-settings-hint">
      Notifications are blocked at the browser level. Enable them
      in your browser site settings, then come back here.
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
  const popover = open && (
    <div className="notification-settings-popover">
      <div className="notification-settings-row notification-settings-master">
        <span>Browser notifications</span>
        <button
          type="button"
          onClick={onToggle}
          disabled={masterDisabled}
        >
          {masterLabel}
        </button>
      </div>
      {permissionHint}
      <div className="notification-settings-divider" />
      {kindRows}
    </div>
  );

  function togglePopover() {
    setOpen((v) => { return !v; });
  }
  return (
    <div className="notification-settings" ref={popoverRef}>
      <button
        type="button"
        title="Notification settings"
        onClick={togglePopover}
        disabled={!supported}
      >
        <Icon name="gear" />
      </button>
      {popover}
    </div>
  );
}
