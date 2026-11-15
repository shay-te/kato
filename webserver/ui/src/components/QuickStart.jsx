import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cancelClaudeSignIn,
  fetchClaudeSignIn,
  fetchQuickStart,
  fetchQuickStartChecks,
  openGitInstaller,
  saveQuickStart,
  sendClaudeSignInCode,
  startClaudeSignIn,
} from '../api.js';
import { useAgentUpgrade } from '../hooks/useAgentUpgrade.js';
import { cx } from '../utils/cx.js';
import {
  CLAUDE_STATE,
  allChecksPass,
  claudeState,
  homeRelative,
  initialFolder,
  repositoriesText,
  startBlocker,
} from '../utils/quickStart.js';
import FolderBrowser from './FolderBrowser.jsx';
import Icon, { BusyIcon } from './Icon.jsx';

// The first run: two checks and a folder, each found or fixed from here, then
// Start. Nothing is typed unless kato could not find it: Claude Code (installed
// and signed in from this screen — no terminal), git, and the folder holding
// the most repositories. No ticket system: tasks are written in kato, and a
// tracker is connected later (the link at the bottom opens the full wizard).
//
// While something is not ready the checks are asked again every few seconds,
// so a sign-in finished in the browser, or a git install, turns green alone.
const CHECK_EVERY_MS = 2500;

export default function QuickStart({ onStarted, onUseWizard }) {
  const [screen, setScreen] = useState(null);
  const [checks, setChecks] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [folder, setFolder] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchQuickStart()
      .then((body) => {
        if (cancelled) { return; }
        setScreen(body);
        setChecks({ claude: body.claude, git: body.git });
        setFolder(initialFolder(body));
      })
      .catch((error) => { if (!cancelled) { setLoadError(String(error?.message || error)); } });
    return () => { cancelled = true; };
  }, []);

  const refreshChecks = useCallback(async () => {
    try {
      setChecks(await fetchQuickStartChecks());
    } catch {
      // The next tick asks again; a missed one is not a finding.
    }
  }, []);

  const passing = allChecksPass(checks);
  useEffect(() => {
    if (!screen || passing) { return undefined; }
    const timer = setInterval(refreshChecks, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [screen, passing, refreshChecks]);

  if (loadError) {
    return (
      <div className="quick-start">
        <p className="setup-wizard-error" role="alert">Could not look at this computer: {loadError}</p>
      </div>
    );
  }
  if (!screen || !checks) {
    return <div className="quick-start"><p className="quick-start-note" role="status">Looking at this computer…</p></div>;
  }

  const blocker = startBlocker(checks, folder);
  const start = async () => {
    setStarting(true);
    setStartError('');
    const saved = await saveQuickStart({ folder: folder.path, create: folder.create });
    if (!saved.ok) {
      setStarting(false);
      setStartError(saved.body?.error || saved.error || 'kato could not save it');
      return;
    }
    onStarted?.();
  };

  return (
    <div className="quick-start">
      <ol className="quick-start-steps">
        <ClaudeStep claude={checks.claude} onChanged={refreshChecks} />
        <GitStep git={checks.git} onChanged={refreshChecks} />
        <FolderStep screen={screen} folder={folder} onPick={setFolder} />
      </ol>
      {startError && <p className="setup-wizard-error" role="alert">{startError}</p>}
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

function ClaudeStep({ claude, onChanged }) {
  const state = claudeState(claude);
  const { progress, start: install } = useAgentUpgrade('claude');
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

  if (state === CLAUDE_STATE.READY || state === CLAUDE_STATE.INSTALLED) {
    return (
      <Step state="ready" title="Claude Code">
        <p className="quick-start-note">
          {state === CLAUDE_STATE.READY ? 'Installed and signed in' : 'Installed'} — version {claude.version}.
        </p>
      </Step>
    );
  }
  if (state === CLAUDE_STATE.SIGNED_OUT) {
    return (
      <Step state="todo" title="Claude Code">
        <SignIn onChanged={onChanged} />
      </Step>
    );
  }
  const plan = claude?.install || {};
  return (
    <Step state={installing ? 'busy' : 'todo'} title="Claude Code">
      {installing ? (
        <div className="quick-start-progress" role="status">
          <progress max="100" value={progress.percent || 0} aria-label="Installing Claude Code" />
          <span className="quick-start-note">{progress.step || 'Installing…'}</span>
        </div>
      ) : (
        <>
          <p className="quick-start-note">Kato works through Claude Code, and it is not on this computer yet.</p>
          {progress?.state === 'error' && (
            <p className="setup-wizard-error" role="alert">The install did not finish: {progress.message}</p>
          )}
          {plan.available ? (
            <div className="quick-start-actions">
              <button type="button" className="setup-wizard-btn setup-wizard-btn--primary" onClick={install}>
                Install Claude Code
              </button>
              <code className="quick-start-command" title="What kato runs">{plan.command}</code>
            </div>
          ) : (
            <p className="quick-start-note">
              {plan.reason ? `Kato cannot install it here: ${plan.reason}. ` : ''}
              Install it from{' '}
              <a href="https://code.claude.com/docs/en/setup" target="_blank" rel="noreferrer">code.claude.com/docs/en/setup</a>
              , and this turns green by itself.
            </p>
          )}
        </>
      )}
    </Step>
  );
}

// Claude Code's own sign-in, run by kato: the browser opens to sign in; if it
// does not (inside WSL it usually cannot), the link is here, and a code the
// sign-in page shows is pasted here and handed to the CLI.
function SignIn({ onChanged }) {
  const [signIn, setSignIn] = useState(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const waiting = signIn?.state === 'waiting';

  useEffect(() => {
    if (!waiting) { return undefined; }
    const timer = setInterval(async () => {
      try {
        const next = await fetchClaudeSignIn();
        setSignIn(next);
        if (next.state !== 'waiting') { onChanged(); }
      } catch {
        // asked again next tick
      }
    }, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [waiting, onChanged]);

  const begin = async () => {
    setError('');
    const result = await startClaudeSignIn();
    if (!result.ok) { setError(result.body?.error || result.error || 'could not start the sign-in'); return; }
    setSignIn(result.body);
  };
  const send = async () => {
    setError('');
    const result = await sendClaudeSignInCode(code.trim());
    if (!result.ok) { setError(result.body?.error || result.error || 'the code was not taken'); return; }
    setCode('');
    setSignIn(result.body);
  };

  if (!waiting) {
    return (
      <>
        <p className="quick-start-note">Installed — now sign in with your Claude account.</p>
        {signIn?.state === 'failed' && <p className="setup-wizard-error" role="alert">{signIn.message}</p>}
        {error && <p className="setup-wizard-error" role="alert">{error}</p>}
        <div className="quick-start-actions">
          <button type="button" className="setup-wizard-btn setup-wizard-btn--primary" onClick={begin}>
            Sign in to Claude
          </button>
        </div>
      </>
    );
  }
  return (
    <div className="quick-start-sign-in">
      <p className="quick-start-note" role="status">
        A browser window opened to sign in.
        {signIn.link && (
          <>
            {' '}Didn’t open?{' '}
            <a href={signIn.link} target="_blank" rel="noreferrer">Open the sign-in page</a>.
          </>
        )}
      </p>
      {signIn.asks_for_code && (
        <div className="quick-start-code">
          <input
            type="text"
            className="quick-start-input"
            aria-label="Code from the sign-in page"
            placeholder="If the page shows a code, paste it here"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <button type="button" className="setup-wizard-btn" disabled={!code.trim()} onClick={send}>
            Send code
          </button>
        </div>
      )}
      {error && <p className="setup-wizard-error" role="alert">{error}</p>}
      <button type="button" className="quick-start-link" onClick={async () => setSignIn((await cancelClaudeSignIn()).body)}>
        Cancel
      </button>
    </div>
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

function FolderStep({ screen, folder, onPick }) {
  const [browsing, setBrowsing] = useState(false);
  const found = screen.folders || [];
  const offered = found.length ? found : [{ path: screen.new_folder, repositories: 0, create: true }];
  const chosenIsListed = offered.some((entry) => entry.path === folder?.path);
  return (
    <Step state={folder?.path ? 'ready' : 'todo'} title="Your code">
      <p className="quick-start-note">
        {found.length
          ? 'The folder kato works in. These hold git repositories:'
          : 'No repositories found on this computer yet. Kato creates this folder — clone your repositories into it:'}
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
      <button type="button" className="quick-start-link" onClick={() => setBrowsing((open) => !open)}>
        {browsing ? 'Close' : 'Another folder…'}
      </button>
      {browsing && (
        <FolderBrowser
          initialPath={folder?.path || screen.home || '~'}
          onPick={(path) => { onPick({ path, create: false }); setBrowsing(false); }}
          onClose={() => setBrowsing(false)}
        />
      )}
    </Step>
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
