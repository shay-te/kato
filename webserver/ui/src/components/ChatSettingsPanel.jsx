import { useEffect, useState } from 'react';
import {
  readSteerWhileWorking,
  writeSteerWhileWorking,
  subscribeSteerWhileWorking,
} from '../utils/composerSteerPref.js';
import {
  readUltracodeByDefault,
  writeUltracodeByDefault,
  subscribeUltracodeByDefault,
} from '../utils/ultracodeDefaultPref.js';
import { useAgentVersion } from '../hooks/useAgentVersion.js';

// Settings → Chat. Operator preference for what happens to a message sent
// while Claude is still working on the previous turn. Pure client-side
// (localStorage) — the delivery transport is identical either way; only the
// composer's default decision changes. See utils/composerSteerPref.js.
export default function ChatSettingsPanel() {
  const [steer, setSteer] = useState(() => readSteerWhileWorking());
  useEffect(() => subscribeSteerWhileWorking(setSteer), []);

  const [ultracodeByDefault, setUltracodeByDefault] = useState(
    () => readUltracodeByDefault(),
  );
  useEffect(() => subscribeUltracodeByDefault(setUltracodeByDefault), []);
  // Same gate the composer chip uses: only offer this where the installed
  // agent CLI actually runs multi-agent workflows, or the setting promises
  // something the keyword cannot deliver.
  const agentVersion = useAgentVersion();
  const supportsWorkflows = !!(agentVersion && agentVersion.supports_workflows);

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

      {supportsWorkflows && (
        <fieldset className="chat-settings-fieldset">
          <legend className="chat-settings-legend">Workflow mode</legend>

          <label
            className="chat-settings-option"
            title="Start the composer's ultracode chip switched on for tasks you haven't toggled it on yourself. Tasks where you already chose keep your choice."
          >
            <input
              type="checkbox"
              checked={ultracodeByDefault}
              onChange={(event) => writeUltracodeByDefault(event.target.checked)}
            />
            <span className="chat-settings-option-text">
              <span className="chat-settings-option-label">
                Turn on ultracode for new tasks
              </span>
              <span className="chat-settings-option-hint">
                Prepends the <code>ultracode</code> keyword so Claude authors
                and runs multi-agent workflows at high effort. It can fan out
                to many agents and cost significantly more tokens, so it stays
                off unless you ask for it. Tasks where you already flipped the
                chip yourself keep that choice.
              </span>
            </span>
          </label>
        </fieldset>
      )}
    </div>
  );
}
