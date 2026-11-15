import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchQuickStart,
  fetchQuickStartChecks,
  openGitInstaller,
  saveQuickStart,
} from '../api.js';
import { useAgentUpgrade } from '../hooks/useAgentUpgrade.js';
import { useNativeFolderDialog } from '../hooks/useNativeFolderDialog.js';
import { cx } from '../utils/cx.js';
import {
  AGENT_STATE,
  QUICK_START_AGENTS,
  agentLabel,
  agentState,
  agentUsable,
  allChecksPass,
  homeRelative,
  initialFolder,
  repositoriesText,
  missingSettingText,
  startBlocker,
} from '../utils/quickStart.js';
import BrowseFolderButton from './BrowseFolderButton.jsx';
import ClaudeSignIn from './ClaudeSignIn.jsx';
import Icon, { BusyIcon } from './Icon.jsx';

// The first run: the agent the operator uses, git, and the folder their code is
// in — then Start. The OPERATOR picks the agent (chips: Claude Code, Codex,
// Grok, OpenCode) and the folder; kato never picks for them. It checks the
// picked agent, installs it where it can, and runs Claude Code's sign-in from
// here (the others name their own). No ticket system: tasks are written in
// kato, and a tracker is connected later (the link at the bottom opens the full
// wizard).
//
// While something is not ready the checks are asked again every few seconds,
// so a sign-in finished in the browser, or an install, turns green alone.
const CHECK_EVERY_MS = 2500;

export default function QuickStart({ onStarted, onUseWizard }) {
  const [screen, setScreen] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [agent, setAgent] = useState('');
  const [statuses, setStatuses] = useState({});
  const [git, setGit] = useState(null);
  const [folder, setFolder] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stillMissing, setStillMissing] = useState([]);
  const [startError, setStartError] = useState('');
  const agentRef = useRef('');
  agentRef.current = agent;

  useEffect(() => {
    let cancelled = false;
    fetchQuickStart()
      .then((body) => {
        if (cancelled) { return; }
        setScreen(body);
        setStatuses(body.agents || {});
        setGit(body.git || null);
        setAgent(body.agent || '');
        setFolder(initialFolder(body));
      })
      .catch((error) => { if (!cancelled) { setLoadError(String(error?.message || error)); } });
    return () => { cancelled = true; };
  }, []);

  const refreshChecks = useCallback(async () => {
    const which = agentRef.current;
    if (!which) { return; }
    try {
      const next = await fetchQuickStartChecks(which);
      if (agentRef.current !== which) { return; }  // the operator picked another meanwhile
      setStatuses((current) => ({ ...current, [which]: next.agent }));
      setGit(next.git);
    } catch {
      // The next tick asks again; a missed one is not a finding.
    }
  }, []);

  const checks = agent ? { agent: statuses[agent] || null, git } : null;
  const passing = allChecksPass(checks);
  useEffect(() => {
    if (!agent || passing) { return undefined; }
    const timer = setInterval(refreshChecks, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [agent, passing, refreshChecks]);

  if (loadError) {
    return (
      <div className="quick-start">
        <p className="setup-wizard-error" role="alert">Could not look at this computer: {loadError}</p>
      </div>
    );
  }
  if (!screen) {
    return <div className="quick-start"><p className="quick-start-note" role="status">Looking at this computer…</p></div>;
  }

  const pickAgent = (id) => {
    setAgent(id);
    agentRef.current = id;
    refreshChecks();
  };
  const blocker = startBlocker({ agent, checks, folder });
  const start = async () => {
    setStarting(true);
    setStartError('');
    setStillMissing([]);
    const saved = await saveQuickStart({ agent, folder: folder.path, create: folder.create });
    if (!saved.ok) {
      setStarting(false);
      setStartError(saved.body?.error || saved.error || 'kato could not save it');
      return;
    }
    // Saved, yet something quick start never asks (a ticket system set up
    // earlier, half filled in) still keeps kato in setup mode: say what,
    // instead of "Starting kato…" spinning forever.
    const missing = Array.isArray(saved.body?.missing) ? saved.body.missing : [];
    if (missing.length) {
      setStarting(false);
      setStillMissing(missing);
      return;
    }
    onStarted?.();
  };

  return (
    <div className="quick-start">
      <ol className="quick-start-steps">
        <AgentStep
          key={agent || 'none'}
          agent={agent}
          statuses={statuses}
          onPick={pickAgent}
          onChanged={refreshChecks}
        />
        <GitStep git={git} onChanged={refreshChecks} />
        <FolderStep screen={screen} folder={folder} onPick={setFolder} />
      </ol>
      {startError && <p className="setup-wizard-error" role="alert">{startError}</p>}
      {stillMissing.length > 0 && (
        <div className="setup-wizard-connection-error quick-start-still-missing" role="alert">
          <p>Saved — but kato can’t start yet. It still needs:</p>
          <pre>{stillMissing.map(missingSettingText).join('\n')}</pre>
          <button type="button" className="setup-wizard-btn" onClick={onUseWizard}>Fix it in the setup wizard</button>
        </div>
      )}
      <div className="quick-start-go">
        <button
          type="button"
          className="setup-wizard-btn setup-wizard-btn--primary quick-start-start"
          disabled={Boolean(blocker) || starting}
          onClick={start}
        >
          <BusyIcon busy={starting} idle="play" />
          <span>{starting ? 'Starting kato…' : 'Start kato'}</span>
        </button>
        {blocker && !starting && <span className="quick-start-note">{blocker}</span>}
      </div>
      <p className="quick-start-foot">
        Tasks are written in kato. Use Jira, YouTrack, GitHub, GitLab or Bitbucket?{' '}
        <button type="button" className="quick-start-link" onClick={onUseWizard}>
          Connect a ticket system instead
        </button>
        {' '}— or any time later, in Settings.
      </p>
    </div>
  );
}

function Step({ state, title, children }) {
  return (
    <li className={cx('quick-start-step', `is-${state}`)}>
      <span className="quick-start-step-mark" aria-hidden="true">
        {state === 'ready' && <Icon name="check" />}
        {state === 'busy' && <BusyIcon busy idle="dot" />}
        {state === 'todo' && <Icon name="warning" />}
      </span>
      <div className="quick-start-step-body">
        <h3 className="quick-start-step-title">{title}</h3>
        {children}
      </div>
    </li>
  );
}

// The agent chips, then what the picked agent needs. Remounted per agent (its
// key), so the install progress always belongs to the agent on screen; the
// chips are locked while an install runs — one install at a time on a host.
function AgentStep({ agent, statuses, onPick, onChanged }) {
  const { progress, start: install } = useAgentUpgrade(agent);
  const installing = progress?.state === 'running';
  const doneInstalling = useRef(false);
  useEffect(() => {
    // The binary just changed: ask again at once rather than at the next tick.
    if (progress && progress.state !== 'running' && !doneInstalling.current) {
      doneInstalling.current = true;
      onChanged();
    }
    if (progress?.state === 'running') { doneInstalling.current = false; }
  }, [progress, onChanged]);

  const status = agent ? statuses[agent] : null;
  let state = 'todo';
  if (installing) { state = 'busy'; } else if (agent && agentUsable(status)) { state = 'ready'; }
  return (
    <Step state={state} title="Your agent">
      <div className="quick-start-chips" role="radiogroup" aria-label="Your agent">
        {QUICK_START_AGENTS.map((choice) => (
          <button
            key={choice.id}
            type="button"
            role="radio"
            aria-checked={agent === choice.id}
            className={cx('quick-start-chip', agent === choice.id && 'is-picked')}
            disabled={installing && agent !== choice.id}
            onClick={() => onPick(choice.id)}
          >
            {choice.label}
            {statuses[choice.id]?.installed && <span className="quick-start-chip-installed" title="Installed on this computer">installed</span>}
          </button>
        ))}
      </div>
      {agent
        ? <AgentSetup agent={agent} status={status} progress={progress} install={install} onChanged={onChanged} />
        : <p className="quick-start-note">Pick the one you use — kato checks it is installed and signed in.</p>}
    </Step>
  );
}

function AgentSetup({ agent, status, progress, install, onChanged }) {
  const label = agentLabel(agent);
  const state = agentState(status);
  if (state === AGENT_STATE.READY || state === AGENT_STATE.INSTALLED) {
    return (
      <>
        <p className="quick-start-note">
          {state === AGENT_STATE.READY ? 'Installed and signed in' : 'Installed'} — version {status.version}.
        </p>
        {status.login_command && (
          <p className="quick-start-note">
            Signed in already? If not, sign in once with <code className="quick-start-command">{status.login_command}</code>.
          </p>
        )}
      </>
    );
  }
  if (state === AGENT_STATE.SIGNED_OUT) {
    return <ClaudeSignIn onChanged={onChanged} note="Installed — sign in with your Claude account now, or later: kato asks again on its main screen." />;
  }
  if (progress?.state === 'running') {
    return (
      <div className="quick-start-progress" role="status">
        <progress max="100" value={progress.percent || 0} aria-label={`Installing ${label}`} />
        <span className="quick-start-note">{progress.step || 'Installing…'}</span>
      </div>
    );
  }
  const plan = status?.install || {};
  return (
    <>
      <p className="quick-start-note">{label} is not on this computer yet.</p>
      {progress?.state === 'error' && (
        <p className="setup-wizard-error" role="alert">The install did not finish: {progress.message}</p>
      )}
      {plan.available ? (
        <div className="quick-start-actions">
          <button type="button" className="setup-wizard-btn setup-wizard-btn--primary" onClick={install}>
            Install {label}
          </button>
          <code className="quick-start-command" title="What kato runs">{plan.command}</code>
        </div>
      ) : (
        <>
          {status?.help && <pre className="quick-start-help">{status.help.trim()}</pre>}
          <p className="quick-start-note">
            {status?.download && (
              <>Get it from <a href={status.download} target="_blank" rel="noreferrer">{status.download}</a>
                {' '}— this turns green by itself once it is installed.</>
            )}
          </p>
        </>
      )}
    </>
  );
}

function GitStep({ git, onChanged }) {
  const [opened, setOpened] = useState(false);
  if (git?.installed) {
    return (
      <Step state="ready" title="git">
        <p className="quick-start-note">Installed — version {git.version}.</p>
      </Step>
    );
  }
  const install = git?.install || {};
  const openInstaller = async () => {
    const result = await openGitInstaller();
    setOpened(result.ok);
    onChanged();
  };
  return (
    <Step state="todo" title="git">
      <p className="quick-start-note">Kato needs git to work in your repositories. {install.text}</p>
      <div className="quick-start-actions">
        {install.can_start && (
          <button type="button" className="setup-wizard-btn setup-wizard-btn--primary" onClick={openInstaller}>
            {opened ? 'Installer opened — finish it there' : 'Install git'}
          </button>
        )}
        <code className="quick-start-command">{install.command}</code>
      </div>
    </Step>
  );
}

// The folder: the suggestions found, none picked; Browse… opens the COMPUTER'S
// OWN folder dialog (kato opens it — a page cannot). Kato has no folder browser
// of its own: where no dialog can open, the folder's path is typed.
function FolderStep({ screen, folder, onPick }) {
  const dialog = useNativeFolderDialog();
  const found = screen.folders || [];
  const offered = found.length ? found : [{ path: screen.new_folder, repositories: 0, create: true }];
  const chosenIsListed = offered.some((entry) => entry.path === folder?.path);
  // Never a folder that does not exist yet (the one kato offers to create).
  const start = (folder && !folder.create && folder.path) || screen.home || '';
  const pick = (path) => onPick({ path, create: false });
  // Typed only once kato SAYS no dialog can open here (an older kato says nothing: Browse… it is).
  const typing = screen.folder_dialog === false || dialog.unavailable;

  return (
    <Step state={folder?.path ? 'ready' : 'todo'} title="Your code">
      <p className="quick-start-note">
        {found.length
          ? 'Pick the folder kato works in. These hold git repositories:'
          : 'No repositories found on this computer yet. Pick a folder, or let kato create this one and clone your repositories into it:'}
      </p>
      <div className="quick-start-folders" role="radiogroup" aria-label="Your code folder">
        {offered.map((entry) => (
          <FolderChoice
            key={entry.path}
            path={homeRelative(entry.path, screen.home)}
            detail={entry.create ? 'new folder' : repositoriesText(entry.repositories)}
            picked={folder?.path === entry.path}
            onPick={() => onPick({ path: entry.path, create: Boolean(entry.create) })}
          />
        ))}
        {folder?.path && !chosenIsListed && (
          <FolderChoice path={homeRelative(folder.path, screen.home)} detail="your pick" picked onPick={() => {}} />
        )}
      </div>
      {typing ? (
        <TypedFolder onPick={pick} />
      ) : (
        <div className="quick-start-actions">
          <BrowseFolderButton
            dialog={dialog}
            start={start}
            prompt="Choose the folder your code is in"
            className="setup-wizard-btn"
            onPick={pick}
          />
        </div>
      )}
      {dialog.error && <p className="browse-folder-error" role="alert">{dialog.error}</p>}
    </Step>
  );
}

// No folder dialog can open on this computer (one with no screen): its path is typed.
function TypedFolder({ onPick }) {
  const [path, setPath] = useState('');
  const use = (event) => {
    event.preventDefault();
    if (path.trim()) { onPick(path.trim()); }
  };
  return (
    <form className="quick-start-typed-folder" onSubmit={use}>
      <input
        type="text"
        className="setup-wizard-input"
        aria-label="Folder path"
        placeholder="~/code"
        value={path}
        onChange={(event) => setPath(event.target.value)}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
      />
      <button type="submit" className="setup-wizard-btn" disabled={!path.trim()}>Use this folder</button>
    </form>
  );
}

function FolderChoice({ path, detail, picked, onPick }) {
  return (
    <label className={cx('quick-start-folder', picked && 'is-picked')}>
      <input type="radio" name="quick-start-folder" aria-label={`${path} — ${detail}`} checked={picked} onChange={onPick} />
      <span className="quick-start-folder-path">{path}</span>
      <span className="quick-start-folder-count">{detail}</span>
    </label>
  );
}
