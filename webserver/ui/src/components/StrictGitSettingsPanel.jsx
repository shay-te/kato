import SchemaFieldGroup from './settings/SchemaFieldGroup.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsSectionHead from './settings/SettingsSectionHead.jsx';

// "Strict git" tab — one setting, explained.
//
// The switch itself is the schema field ``KATO_AGENT_GIT_STRICT`` lifted out
// of General (``panel: 'strict-git'``), so its label, help, default-on and the
// confirm popup on the way off all stay in the schema. This tab adds what a
// single toggle row cannot: what strict git is, why it is the recommended
// setting, and exactly what turning it off changes — for Claude and Codex.

const WHY_RECOMMENDED = [
  ['One hand on the branch.',
    'Push, the pull request, the Changes tab, the review loop and plan '
    + 'sub-tasks all read the task\'s branch. When only kato moves it, what you '
    + 'reviewed is exactly what gets pushed.'],
  ['Nothing disappears by accident.',
    'The agent cannot reset, rebase or amend work away. The most it can do is '
    + 'change a file, and every change shows in the Changes tab.'],
  ['Predictable history.',
    'Kato makes the commits, at one moment, in one format — or in your '
    + 'repository\'s own format if "Commits/PRs follow the repo\'s own '
    + 'conventions" is on.'],
  ['Fewer interruptions.',
    'A commit or a branch move never needs your approval, because the agent '
    + 'never asks to make one.'],
];

const WHEN_OFF = [
  'The agent may commit, amend, squash, rebase, reset, merge, cherry-pick and '
    + 'tag on the task\'s branch.',
  'Push, Pull, Merge master, Update source, Pull request and Done wait while '
    + 'the agent is working, so two programs never run git in one repository '
    + 'at once.',
  'If the agent rewrites commits that were already pushed, kato does not push '
    + 'the branch until they are put back. It never force-pushes.',
  'git reset --hard always asks first, and "Allow always" on git reset does '
    + 'not cover it.',
];

const NEVER = [
  'Push, pull, fetch or anything else that talks to the remote. Only kato '
    + 'pushes, and only when you press Push or Done.',
  'Switch to another branch or create one.',
  'Change git config.',
  'In Auto mode it is always strict: nobody is there to see a prompt.',
];

function Points({ items }) {
  return (
    <ul className="strict-git-points">
      {items.map((item) => (
        Array.isArray(item)
          ? <li key={item[0]}><strong>{item[0]}</strong> {item[1]}</li>
          : <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

export default function StrictGitSettingsPanel({ highlightKey = '' }) {
  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Strict git">
        <p>
          Who runs git in a task&apos;s repositories: kato alone, or kato and
          the agent. On by default, and recommended.
        </p>
      </SettingsPanelHead>

      <SettingsSectionHead title="What strict git is">
        <p className="settings-drawer-field-group-help">
          With strict git on, the agent only works on files: it edits them,
          stages them, stashes them and undoes its own changes. Kato does
          everything else — it makes the commits, keeps every repository on the
          task&apos;s branch, and pushes only when you press Push or Done.
        </p>
      </SettingsSectionHead>

      <SchemaFieldGroup sectionId="general" panel="strict-git" highlightKey={highlightKey} />

      <SettingsSectionHead title="Why it is recommended">
        <Points items={WHY_RECOMMENDED} />
      </SettingsSectionHead>

      <SettingsSectionHead title="When to turn it off">
        <p className="settings-drawer-field-group-help">
          When you drive the chat yourself and want the agent to shape the
          history: &ldquo;commit this&rdquo;, &ldquo;split that into two
          commits&rdquo;, &ldquo;squash these&rdquo;, &ldquo;rebase onto
          master&rdquo;. Turning it off asks you to confirm first.
        </p>
      </SettingsSectionHead>

      <SettingsSectionHead title="What changes when it is off">
        <Points items={WHEN_OFF} />
      </SettingsSectionHead>

      <SettingsSectionHead title="What the agent never does, in either mode">
        <Points items={NEVER} />
      </SettingsSectionHead>

      <SettingsSectionHead title="Claude and Codex">
        <p className="settings-drawer-field-group-help">
          The setting applies to both. A running chat switches on its next
          message. Claude&apos;s git commands are refused by the Claude CLI
          itself and by kato&apos;s Action Guard. Codex&apos;s own sandbox keeps
          each repository&apos;s <code>.git</code> read-only while strict git is
          on — even staging fails there — and writable when it is off. Codex
          has no per-command block, so staying on the branch and not pushing
          are instructions it is given, not walls.
        </p>
      </SettingsSectionHead>
    </div>
  );
}
