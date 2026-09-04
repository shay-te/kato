import { useEffect, useState } from 'react';
import {
  readPermissionSoundPrefs,
  writePermissionSoundPrefs,
  subscribePermissionSoundPrefs,
  playPermissionChime,
} from '../utils/permissionSound.js';

// Self-contained sound-on-approval settings. Reads/writes its own
// localStorage-backed store (utils/permissionSound.js) so it can drop into
// the shared NotificationPrefsBody without threading props through the
// browser-notification hook — the two are independent concerns (a sound can
// play even when browser notifications are off/denied).
export default function PermissionSoundSettings() {
  const [prefs, setPrefs] = useState(() => readPermissionSoundPrefs());
  useEffect(() => subscribePermissionSoundPrefs(setPrefs), []);

  function update(patch) {
    writePermissionSoundPrefs({ ...prefs, ...patch });
  }

  return (
    <div className="notification-sound-settings">
      <label
        className="notification-settings-row notification-settings-master"
        title="Play a light chime when a task needs your approval."
      >
        {/* Names the TOGGLE, not the group — in the settings panel the group
          * is titled "Approval sound" above it, and repeating that here read
          * as a heading printed twice. The tooltip carries the full sentence
          * for the header popover, which has no heading. */}
        <span className="notification-settings-master-label">
          Play a chime
          <span className="notification-settings-master-state">
            {prefs.enabled ? 'on' : 'off'}
          </span>
        </span>
        <input
          type="checkbox"
          checked={prefs.enabled}
          onChange={(event) => update({ enabled: event.target.checked })}
        />
      </label>
      <label
        className="notification-settings-row"
        title="Only chime when the kato tab or window isn't focused — so it nudges you when you've looked away, and stays quiet while you're watching. Turn off to always chime."
      >
        <input
          type="checkbox"
          checked={prefs.onlyWhenUnfocused}
          onChange={(event) => update({ onlyWhenUnfocused: event.target.checked })}
          disabled={!prefs.enabled}
        />
        <span>Only when kato isn&apos;t focused</span>
      </label>
      <button
        type="button"
        className="notification-sound-test"
        onClick={playPermissionChime}
        disabled={!prefs.enabled}
      >
        Test sound
      </button>
    </div>
  );
}
