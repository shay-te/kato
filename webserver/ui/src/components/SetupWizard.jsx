import { useEffect, useMemo, useState } from 'react';
import {
  fetchTaskProviders,
  updateTaskProvider,
  fetchSettings,
  updateSettings,
} from '../api.js';
import { isSecretKey } from '../utils/providerFields.js';
import { toast } from '../stores/toastStore.js';

// First-run setup wizard. One action per step (the operator asked for a
// wizard, not the full settings drawer): pick the ticket system, then enter
// its details, then point kato at the repositories folder. Every save lands
// in ``~/.kato/settings.json`` (never the operator's ``.env``) and the gate's
// live config-status poll flips the wizard to "all set" the moment kato has
// everything it needs — no terminal, no restart to satisfy the check.

const TICKET_SYSTEMS = [
  { id: 'youtrack', label: 'YouTrack', blurb: 'JetBrains YouTrack issues' },
  { id: 'jira', label: 'Jira', blurb: 'Atlassian Jira issues' },
  { id: 'github', label: 'GitHub Issues', blurb: 'Issues on GitHub' },
  { id: 'gitlab', label: 'GitLab Issues', blurb: 'Issues on GitLab' },
  { id: 'bitbucket', label: 'Bitbucket Issues', blurb: 'Issues on Bitbucket' },
];

// The fields kato needs to poll each ticket system. Mirrors the backend's
// required-key map (validate_env.REQUIRED_AGENT_KEYS_BY_PLATFORM) plus the
// token/email you can't authenticate without. Filling these clears the
// ticket portion of ``/api/config-status``'s ``missing`` list.
const REQUIRED_TICKET_FIELDS = {
  youtrack: [
    'YOUTRACK_API_BASE_URL', 'YOUTRACK_API_TOKEN',
    'YOUTRACK_PROJECT', 'YOUTRACK_ASSIGNEE',
  ],
  jira: [
    'JIRA_API_BASE_URL', 'JIRA_API_TOKEN', 'JIRA_EMAIL',
    'JIRA_PROJECT', 'JIRA_ASSIGNEE',
  ],
  github: [
    'GITHUB_API_BASE_URL', 'GITHUB_API_TOKEN',
    'GITHUB_OWNER', 'GITHUB_REPO', 'GITHUB_ASSIGNEE',
  ],
  gitlab: [
    'GITLAB_API_BASE_URL', 'GITLAB_API_TOKEN',
    'GITLAB_PROJECT', 'GITLAB_ASSIGNEE',
  ],
  bitbucket: [
    'BITBUCKET_API_BASE_URL', 'BITBUCKET_API_TOKEN', 'BITBUCKET_USERNAME',
    'BITBUCKET_API_EMAIL', 'BITBUCKET_WORKSPACE', 'BITBUCKET_REPO_SLUG',
    'BITBUCKET_ASSIGNEE',
  ],
};

const STEP_TITLES = ['Ticket system', 'Ticket details', 'Repositories', 'Finish'];

// "YOUTRACK_API_BASE_URL" → "API base URL" (drop the platform prefix, keep
// well-known acronyms readable) so a first-comer sees friendly labels.
export function humanizeFieldKey(key, platform) {
  const prefix = `${String(platform).toUpperCase()}_`;
  let text = String(key).startsWith(prefix) ? key.slice(prefix.length) : key;
  text = text.replace(/_/g, ' ').toLowerCase();
  text = text.replace(/\bapi\b/g, 'API').replace(/\burl\b/g, 'URL');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export default function SetupWizard({ status, onRefreshStatus, onOpenFullSettings }) {
  const [step, setStep] = useState(0);
  const [platform, setPlatform] = useState('');
  const [providers, setProviders] = useState({});
  const [ticketDraft, setTicketDraft] = useState({});
  const [repoRoot, setRepoRoot] = useState('');
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');

  // Load the current provider field values + repository root once. Secret
  // values are intentionally NOT seeded into the draft below (masked, shown
  // as "already set"), so we never echo a token back into the DOM.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const providersResult = await fetchTaskProviders();
      if (cancelled) { return; }
      if (providersResult.ok) {
        setProviders(providersResult.body?.providers || {});
        setPlatform((current) => current || String(providersResult.body?.active || 'youtrack'));
      } else {
        setLoadError('Could not load ticket-system options — check the server logs.');
      }
      const settingsResult = await fetchSettings();
      if (cancelled) { return; }
      if (settingsResult.ok) {
        setRepoRoot(String(settingsResult.body?.repository_root_path?.value || ''));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const requiredKeys = REQUIRED_TICKET_FIELDS[platform] || [];
  const fields = providers?.[platform]?.fields || {};
  const serverHasValue = (key) => Boolean(fields[key]?.value);

  // Re-seed the ticket draft whenever the selected platform (or the loaded
  // server values) change. Non-secret fields pre-fill so the operator can
  // see/confirm them; secret fields start blank with a "set" placeholder.
  // Values the operator ALREADY TYPED always win over the seed — the
  // mount-time providers fetch can resolve after typing has started, and
  // re-running this effect must not wipe their work. (Platform switches are
  // naturally fresh: every platform's keys are prefix-disjoint.)
  useEffect(() => {
    if (!platform) { return; }
    const platformFields = providers?.[platform]?.fields || {};
    setTicketDraft((current) => {
      const seed = {};
      for (const key of (REQUIRED_TICKET_FIELDS[platform] || [])) {
        const typed = (current[key] || '').trim();
        const field = platformFields[key] || {};
        seed[key] = typed ? current[key] : (isSecretKey(key) ? '' : (field.value || ''));
      }
      return seed;
    });
  }, [platform, providers]);

  const canSaveTicket = requiredKeys.length > 0 && requiredKeys.every(
    (key) => (ticketDraft[key] || '').trim() || serverHasValue(key),
  );

  const runSave = async (doSave) => {
    setSaving(true);
    try {
      const result = await doSave();
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: 'Save failed', fallback: 'could not save',
        });
        return false;
      }
      return true;
    } finally {
      setSaving(false);
    }
  };

  const onSaveTicket = async () => {
    // Only send fields the operator actually typed, so blank inputs never
    // clobber a value already present in settings.json / .env.
    const payloadFields = {};
    for (const key of requiredKeys) {
      const value = (ticketDraft[key] || '').trim();
      if (value) { payloadFields[key] = value; }
    }
    const ok = await runSave(() => updateTaskProvider({
      active: platform, provider: platform, fields: payloadFields,
    }));
    if (!ok) { return; }
    const label = TICKET_SYSTEMS.find((s) => s.id === platform)?.label || platform;
    toast.show({ kind: 'success', title: 'Ticket system saved', message: `${label} connected.` });
    await onRefreshStatus();
    setStep(2);
  };

  const onSaveRepo = async () => {
    const path = repoRoot.trim();
    if (!path) { return; }
    const ok = await runSave(() => updateSettings({ repository_root_path: path }));
    if (!ok) { return; }
    toast.show({ kind: 'success', title: 'Saved', message: 'Repositories folder set.' });
    await onRefreshStatus();
    setStep(3);
  };

  const missing = useMemo(
    () => (Array.isArray(status?.missing) ? status.missing : []),
    [status],
  );
  // "All set" requires more than a complete config: a start attempt may
  // have FAILED on it (bad token fails connection validation). The gate
  // shows the error banner; here we just must not claim success.
  const isConfigured = Boolean(status) && !status.needs_config && !status.setup_error;

  return (
    <div className="setup-wizard">
      <ol className="setup-wizard-steps" aria-label="Setup progress">
        {STEP_TITLES.map((title, index) => (
          <li
            key={title}
            className={
              'setup-wizard-step-chip'
              + (index === step ? ' is-current' : '')
              + (index < step ? ' is-done' : '')
            }
            aria-current={index === step ? 'step' : undefined}
          >
            <span className="setup-wizard-step-num">{index + 1}</span>
            <span className="setup-wizard-step-title">{title}</span>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <StepPickSystem
          platform={platform}
          onPick={setPlatform}
          onNext={() => setStep(1)}
          loadError={loadError}
        />
      )}

      {step === 1 && (
        <StepTicketDetails
          platform={platform}
          requiredKeys={requiredKeys}
          fields={fields}
          draft={ticketDraft}
          setDraft={setTicketDraft}
          serverHasValue={serverHasValue}
          canSave={canSaveTicket}
          saving={saving}
          onBack={() => setStep(0)}
          onSave={onSaveTicket}
        />
      )}

      {step === 2 && (
        <StepRepositories
          repoRoot={repoRoot}
          setRepoRoot={setRepoRoot}
          saving={saving}
          onBack={() => setStep(1)}
          onSave={onSaveRepo}
        />
      )}

      {step === 3 && (
        <StepFinish
          isConfigured={isConfigured}
          startFailed={Boolean(status?.setup_error)}
          missing={missing}
          onBack={() => setStep(2)}
          onRecheck={onRefreshStatus}
          onOpenFullSettings={onOpenFullSettings}
        />
      )}
    </div>
  );
}

// The Back / "Save & continue" footer shared by the two saving steps.
function WizardSaveActions({ onBack, onSave, canSave, saving }) {
  return (
    <div className="setup-wizard-actions">
      <button type="button" className="setup-wizard-btn" onClick={onBack} disabled={saving}>
        Back
      </button>
      <button
        type="button"
        className="setup-wizard-btn setup-wizard-btn--primary"
        onClick={onSave}
        disabled={!canSave || saving}
      >
        {saving ? 'Saving…' : 'Save & continue'}
      </button>
    </div>
  );
}

// One labelled input row — the shared field shape for every wizard step
// (friendly label + the raw env key + a browser-autofill-proof input).
function WizardField({ label, envKey, type = 'text', value, onChange, placeholder = '' }) {
  return (
    <label className="setup-wizard-field">
      <span className="setup-wizard-field-label">
        {label}
        <code className="setup-wizard-field-key">{envKey}</code>
      </span>
      <input
        type={type}
        className="setup-wizard-input"
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
      />
    </label>
  );
}

function StepPickSystem({ platform, onPick, onNext, loadError }) {
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Where do your tickets live?</h3>
      <p className="setup-wizard-lead">
        Pick the system kato should poll for assigned work. You can change
        this later in Settings.
      </p>
      {loadError && <p className="setup-wizard-error" role="alert">{loadError}</p>}
      <div className="setup-wizard-choices" role="radiogroup" aria-label="Ticket system">
        {TICKET_SYSTEMS.map((system) => (
          <button
            key={system.id}
            type="button"
            role="radio"
            aria-checked={platform === system.id}
            className={'setup-wizard-choice' + (platform === system.id ? ' is-selected' : '')}
            onClick={() => onPick(system.id)}
          >
            <span className="setup-wizard-choice-label">{system.label}</span>
            <span className="setup-wizard-choice-blurb">{system.blurb}</span>
          </button>
        ))}
      </div>
      <div className="setup-wizard-actions">
        <span />
        <button
          type="button"
          className="setup-wizard-btn setup-wizard-btn--primary"
          disabled={!platform}
          onClick={onNext}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function StepTicketDetails({
  platform, requiredKeys, fields, draft, setDraft,
  serverHasValue, canSave, saving, onBack, onSave,
}) {
  const label = TICKET_SYSTEMS.find((s) => s.id === platform)?.label || platform;
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Connect {label}</h3>
      <p className="setup-wizard-lead">
        Enter the details kato needs to read your tickets. Saved to
        {' '}<code>~/.kato/settings.json</code> — your <code>.env</code> is
        left untouched.
      </p>
      <div className="setup-wizard-fields">
        {requiredKeys.map((key) => {
          const secret = isSecretKey(key);
          const alreadySet = serverHasValue(key);
          return (
            <WizardField
              key={key}
              label={humanizeFieldKey(key, platform)}
              envKey={key}
              type={secret ? 'password' : 'text'}
              value={draft[key] || ''}
              onChange={(ev) => setDraft((current) => ({ ...current, [key]: ev.target.value }))}
              placeholder={secret && alreadySet ? '(already set — paste again to replace)' : ''}
            />
          );
        })}
      </div>
      <WizardSaveActions
        onBack={onBack}
        onSave={onSave}
        canSave={canSave}
        saving={saving}
      />
    </div>
  );
}

function StepRepositories({ repoRoot, setRepoRoot, saving, onBack, onSave }) {
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Where are your repositories?</h3>
      <p className="setup-wizard-lead">
        The folder kato scans for <code>.git</code> repositories to clone and
        work in. An absolute path like <code>~/Projects</code>.
      </p>
      <WizardField
        label="Repositories folder"
        envKey="REPOSITORY_ROOT_PATH"
        value={repoRoot}
        onChange={(ev) => setRepoRoot(ev.target.value)}
        placeholder="/Users/you/Projects"
      />
      <WizardSaveActions
        onBack={onBack}
        onSave={onSave}
        canSave={Boolean(repoRoot.trim())}
        saving={saving}
      />
    </div>
  );
}

function StepFinish({ isConfigured, startFailed, missing, onBack, onRecheck, onOpenFullSettings }) {
  return (
    <div className="setup-wizard-body">
      {isConfigured ? (
        <>
          <h3 className="setup-wizard-heading setup-wizard-heading--ok">
            You're all set 🎉
          </h3>
          <p className="setup-wizard-lead">
            Kato has everything it needs and is starting now — this screen
            closes itself as soon as it's running. No restart needed.
          </p>
          <div className="setup-wizard-actions">
            <span />
            <button
              type="button"
              className="setup-wizard-btn"
              onClick={() => window.location.reload()}
            >
              Reload now
            </button>
          </div>
        </>
      ) : (
        <>
          <h3 className="setup-wizard-heading">
            {startFailed ? 'Start failed' : 'Almost there'}
          </h3>
          <p className="setup-wizard-lead">
            {startFailed
              ? 'Kato could not start with the saved settings — see the error '
                + 'above. Fix the values (Back, or the full Settings panel) and '
                + 'kato retries automatically.'
              : 'A few more settings are still required before kato can run. '
                + 'You can fill these in the full Settings panel.'}
          </p>
          {missing.length > 0 && (
            <ul className="setup-wizard-missing">
              {missing.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          <div className="setup-wizard-actions">
            <button type="button" className="setup-wizard-btn" onClick={onBack}>
              Back
            </button>
            <div className="setup-wizard-actions-group">
              {onOpenFullSettings && (
                <button type="button" className="setup-wizard-btn" onClick={onOpenFullSettings}>
                  Open full settings
                </button>
              )}
              <button
                type="button"
                className="setup-wizard-btn setup-wizard-btn--primary"
                onClick={onRecheck}
              >
                Re-check
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
