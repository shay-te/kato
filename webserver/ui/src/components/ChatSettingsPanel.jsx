import { useEffect, useState } from 'react';
import {
  readSteerWhileWorking,
  writeSteerWhileWorking,
  subscribeSteerWhileWorking,
} from '../utils/composerSteerPref.js';

// Settings → Chat. Operator preference for what happens to a message sent
// while Claude is still working on the previous turn. Pure client-side
// (localStorage) — the delivery transport is identical either way; only the
// composer's default decision changes. See utils/composerSteerPref.js.
export default function ChatSettingsPanel() {
  const [steer, setSteer] = useState(() => readSteerWhileWorking());
  useEffect(() => subscribeSteerWhileWorking(setSteer), []);

  return (
    <div className="settings-drawer-panel chat-settings-panel">
      <div className="settings-drawer-panel-head">
        <h3>Chat</h3>
        <p>
          When you send a message while Claude is still working on the previous
          one, kato can hold it in a queue you control (“steer”) or deliver it
          right away.
        </p>
      </div>

      <fieldset className="chat-settings-fieldset">
        <legend className="chat-settings-legend">While Claude is working</legend>

        <label
          className="chat-settings-option"
          title="Hold each message in the per-task queue and deliver it when the current turn ends. You can edit, reorder, or click Steer to send one mid-turn."
        >
          <input
            type="radio"
            name="steer-while-working"
            checked={steer}
            onChange={() => writeSteerWhileWorking(true)}
          />
          <span className="chat-settings-option-text">
            <span className="chat-settings-option-label">
              Steer — hold in the queue
            </span>
            <span className="chat-settings-option-hint">
              Messages wait above the composer until the turn ends. You can
              edit, remove, or click “Steer” to promote one mid-turn.
            </span>
          </span>
        </label>

        <label
          className="chat-settings-option"
          title="Deliver the message to Claude immediately, mid-turn — just like Claude Code in VS Code. Claude reads it on its next pump while it's still working."
        >
          <input
            type="radio"
            name="steer-while-working"
            checked={!steer}
            onChange={() => writeSteerWhileWorking(false)}
          />
          <span className="chat-settings-option-text">
            <span className="chat-settings-option-label">
              Send immediately — like VS Code
            </span>
            <span className="chat-settings-option-hint">
              Claude receives the message right away while it works. No queue,
              no waiting for the turn to end.
            </span>
          </span>
        </label>
      </fieldset>
    </div>
  );
}
