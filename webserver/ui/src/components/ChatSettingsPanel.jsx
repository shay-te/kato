import { useEffect, useState } from 'react';
import {
  APPROVAL_MODE_GLOBAL,
  APPROVAL_MODE_IN_CHAT,
  readApprovalMode,
  subscribeApprovalMode,
  writeApprovalMode,
} from '../utils/approvalModePref.js';
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
// The only backend the server ever reports ``supports_workflows`` for.
const WORKFLOW_CAPABLE_BACKEND = 'claude';

export default function ChatSettingsPanel() {
  const [steer, setSteer] = useState(() => readSteerWhileWorking());
  useEffect(() => subscribeSteerWhileWorking(setSteer), []);
  const [approvalMode, setApprovalMode] = useState(() => readApprovalMode());
  useEffect(() => subscribeApprovalMode(setApprovalMode), []);

  const [ultracodeByDefault, setUltracodeByDefault] = useState(
    () => readUltracodeByDefault(),
  );
  useEffect(() => subscribeUltracodeByDefault(setUltracodeByDefault), []);
  // Gate on whether THIS HOST can run workflows at all — not on whichever
  // task tab happens to be in front.
  //
  // The setting behind this toggle is host-global: one localStorage key, read
  // by every Claude task's composer. So the question it depends on is global
  // too. Keying it to the ACTIVE TASK's backend made a global control vanish
  // from the Settings drawer whenever the operator was looking at a Codex
  // task — they could not switch off an expensive default from where they
  // were standing.
  //
  // Asking about ``claude`` specifically is the honest phrasing of "can this
  // host run workflows": the server sets ``supports_workflows`` only for that
  // backend (agent_version_utils.py), so it is the one CLI whose answer can
  // ever be true. useAgentVersion caches per backend, so this is one probe
  // shared with the composer's own gate rather than a second question.
  const agentVersion = useAgentVersion(WORKFLOW_CAPABLE_BACKEND);
  const supportsWorkflows = !!(agentVersion && agentVersion.supports_workflows);

  return (
    <div className="settings-drawer-panel chat-settings-panel">
      <div className="settings-drawer-panel-head">
        <h3>Chat</h3>
        <p>
          How the chat behaves while an agent is working: when your messages
          reach it, where its approval requests appear, and whether new tasks
          start in workflow mode. Stored per browser.
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

      <fieldset className="chat-settings-fieldset">
        <legend className="chat-settings-legend">
          When the agent asks for approval
        </legend>

        <label
          className="chat-settings-option"
          title="The request appears inside the chat of the task that raised it. Another task's request never covers what you are working on — its tab lights up and it appears in the header, and the request is waiting when you switch to it."
        >
          <input
            type="radio"
            name="approval-mode"
            checked={approvalMode === APPROVAL_MODE_IN_CHAT}
            onChange={() => writeApprovalMode(APPROVAL_MODE_IN_CHAT)}
          />
          <span className="chat-settings-option-text">
            <span className="chat-settings-option-label">
              In the chat that asked
            </span>
            <span className="chat-settings-option-hint">
              Shown between the transcript and the composer, for the task you
              are on. Other tasks light their tab and appear in the
              “waiting for you” list at the top instead of interrupting.
            </span>
          </span>
        </label>

        <label
          className="chat-settings-option"
          title="A dialog over the whole app for ANY task, wherever you are. It interrupts on purpose: an agent stays blocked until you answer."
        >
          <input
            type="radio"
            name="approval-mode"
            checked={approvalMode === APPROVAL_MODE_GLOBAL}
            onChange={() => writeApprovalMode(APPROVAL_MODE_GLOBAL)}
          />
          <span className="chat-settings-option-text">
            <span className="chat-settings-option-label">
              A window over everything
            </span>
            <span className="chat-settings-option-hint">
              Any task’s request opens a dialog wherever you are. It
              interrupts — which is the point: nothing waits unnoticed.
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
