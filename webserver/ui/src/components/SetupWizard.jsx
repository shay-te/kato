import { useEffect, useMemo, useState } from 'react';
import {
  fetchTaskProviders,
  updateTaskProvider,
  fetchSettings,
  updateSettings,
  fetchAllSettings,
  updateAllSettings,
} from '../api.js';
import { isSecretKey } from '../utils/providerFields.js';
import { humanizeFieldKey, fieldPlaceholder, fieldInfo } from '../utils/fieldHelp.js';
import FieldInfoTip from './settings/FieldInfoTip.jsx';
import FolderBrowser from './FolderBrowser.jsx';
import { toast } from '../stores/toastStore.js';

// First-run setup wizard. One action per step (the operator asked for a
// wizard, not the full settings drawer): pick the ticket system, then enter
// its details, then point kato at the repositories folder. Every save lands
// in ``~/.kato/settings.json`` (kato's only config file) and the gate's
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

// The AI agent that runs the work. Only backends kato can actually boot
// with are selectable (validate_env: openhands | claude); OpenRouter is the
// OpenHands runtime pointed at OpenRouter's API. Codex ships as a transport
// lib but is not yet a bootable backend — shown disabled so nobody wedges
// their config on an unsupported value.
const AGENT_CHOICES = [
  {
    id: 'claude',
    backend: 'claude',
    label: 'Claude agent',
    blurb: 'Anthropic Claude Code CLI, running on this machine',
    required: [],
    optional: ['KATO_CLAUDE_MODEL'],
    note: 'Requires the Claude Code CLI installed and logged in on this '
      + 'machine (run `claude login` once in a terminal if you have not).',
  },
  {
    id: 'openhands',
    backend: 'openhands',
    label: 'OpenHands',
    blurb: 'Self-hosted OpenHands server with your own LLM',
    required: [
      'OPENHANDS_BASE_URL', 'OPENHANDS_API_KEY',
      'OH_SECRET_KEY', 'OPENHANDS_LLM_MODEL',
    ],
    optional: ['OPENHANDS_LLM_API_KEY', 'OPENHANDS_LLM_BASE_URL'],
  },
  {
    id: 'openrouter',
    backend: 'openhands',
    label: 'OpenRouter (via OpenHands)',
    blurb: 'OpenHands runtime with a model served by OpenRouter',
    required: [
      'OPENHANDS_BASE_URL', 'OPENHANDS_API_KEY', 'OH_SECRET_KEY',
      'OPENHANDS_LLM_MODEL', 'OPENHANDS_LLM_API_KEY', 'OPENHANDS_LLM_BASE_URL',
    ],
    optional: [],
    prefill: { OPENHANDS_LLM_BASE_URL: 'https://openrouter.ai/api/v1' },
  },
  {
    id: 'codex',
    backend: 'codex',
    label: 'Codex agent',
    blurb: 'Not yet available as a runtime backend',
    required: [],
    optional: [],
    disabled: true,
  },
];

// Bedrock models authenticate with AWS credentials instead of an LLM API
// key: EITHER the bearer token OR the access-key trio (all three together).
const AWS_BEDROCK_KEYS = [
  'AWS_BEARER_TOKEN_BEDROCK', 'AWS_ACCESS_KEY_ID',
  'AWS_SECRET_ACCESS_KEY', 'AWS_REGION_NAME',
];

// Every key any agent choice can read from the server / save.
const AGENT_KEYS = [
  'KATO_AGENT_BACKEND', 'KATO_CLAUDE_MODEL',
  'OPENHANDS_BASE_URL', 'OPENHANDS_API_KEY', 'OH_SECRET_KEY',
  'OPENHANDS_LLM_MODEL', 'OPENHANDS_LLM_API_KEY', 'OPENHANDS_LLM_BASE_URL',
  ...AWS_BEDROCK_KEYS,
];

const STEP_TITLES = [
  'Ticket system', 'Ticket details', 'AI agent', 'Agent details',
  'Repositories', 'Finish',
];

export default function SetupWizard({ status, onRefreshStatus, onOpenFullSettings }) {
  const [step, setStep] = useState(0);
  const [platform, setPlatform] = useState('');
  const [providers, setProviders] = useState({});
  const [ticketDraft, setTicketDraft] = useState({});
  const [repoRoot, setRepoRoot] = useState('');
  const [agentChoice, setAgentChoice] = useState('');
  const [agentDraft, setAgentDraft] = useState({});
  const [agentServer, setAgentServer] = useState({});
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
      // Agent-backend values come from the schema-driven all-settings API
      // (same store the Settings drawer edits). Secrets are never seeded
      // into the draft — knowing THAT they're set is enough.
      const allResult = await fetchAllSettings();
      if (cancelled || !allResult?.ok) { return; }
      const values = {};
      for (const section of (allResult.body?.sections || [])) {
        for (const field of (section.fields || [])) {
          if (AGENT_KEYS.includes(field.key) && !(field.key in values)) {
            values[field.key] = {
              value: String(field.value || ''),
              secret: field.type === 'secret',
            };
          }
        }
      }
      setAgentServer(values);
      const backend = values.KATO_AGENT_BACKEND?.value || '';
      if (backend) {
        setAgentChoice((current) => current || backend);
      }
      setAgentDraft((current) => {
        const seed = { ...current };
        for (const key of AGENT_KEYS) {
          const entry = values[key];
          if (entry && entry.value && !entry.secret && !(seed[key] || '').trim()) {
            seed[key] = entry.value;
          }
        }
        return seed;
      });
    })();
    return () => { cancelled = true; };
  }, []);

  const requiredKeys = REQUIRED_TICKET_FIELDS[platform] || [];
  const fields = providers?.[platform]?.fields || {};
  const serverHasValue = (key) => Boolean(fields[key]?.value);
  // Step 2 shows EVERY field the backend whitelist exposes for the chosen
  // platform (`/api/task-providers` → providers[platform].fields), so the
  // workflow-state settings (progress/review state, issue states) are asked
  // too — for all platforms, without duplicating the catalog client-side.
  // Required connection fields come first and gate the save; the rest are
  // optional (kato has built-in defaults for them).
  const displayedKeys = useMemo(() => {
    const serverKeys = Object.keys(fields);
    return [
      ...requiredKeys,
      ...serverKeys.filter((key) => !requiredKeys.includes(key)),
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, providers]);

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
    const required = REQUIRED_TICKET_FIELDS[platform] || [];
    const keys = [
      ...required,
      ...Object.keys(platformFields).filter((key) => !required.includes(key)),
    ];
    setTicketDraft((current) => {
      const seed = {};
      for (const key of keys) {
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

  const agent = AGENT_CHOICES.find((choice) => choice.id === agentChoice) || null;
  const agentServerHas = (key) => Boolean(agentServer[key]?.value);

  // The agent-details gate mirrors validate_env's conditional rules so that
  // EVERY boot-mandatory key is collected HERE, never discovered at Finish:
  //   - a non-Bedrock model  → OPENHANDS_LLM_API_KEY becomes required
  //   - an openrouter/* model → OPENHANDS_LLM_BASE_URL becomes required
  //   - a bedrock/* model    → AWS fields appear; bearer token OR the
  //     access-key trio must be filled
  const agentGate = useMemo(() => {
    if (!agent) { return { keys: [], required: new Set(), ok: false }; }
    const filled = (key) => Boolean(
      (agentDraft[key] || '').trim() || agentServer[key]?.value,
    );
    const keys = [...(agent.required || []), ...(agent.optional || [])];
    const required = new Set(agent.required || []);
    let bedrockOk = true;
    if (agent.backend === 'openhands') {
      const model = String(
        (agentDraft.OPENHANDS_LLM_MODEL || '').trim()
        || agentServer.OPENHANDS_LLM_MODEL?.value || '',
      ).toLowerCase();
      const isBedrock = model.startsWith('bedrock/');
      const isOpenRouter = model.startsWith('openrouter/');
      if (model && !isBedrock) { required.add('OPENHANDS_LLM_API_KEY'); }
      if (isOpenRouter) { required.add('OPENHANDS_LLM_BASE_URL'); }
      if (isBedrock) {
        for (const key of AWS_BEDROCK_KEYS) {
          if (!keys.includes(key)) { keys.push(key); }
        }
        bedrockOk = filled('AWS_BEARER_TOKEN_BEDROCK') || (
          filled('AWS_ACCESS_KEY_ID')
          && filled('AWS_SECRET_ACCESS_KEY')
          && filled('AWS_REGION_NAME')
        );
      }
    }
    const ok = [...required].every(filled) && bedrockOk;
    return { keys, required, ok };
  }, [agent, agentDraft, agentServer]);

  const onPickAgent = (choice) => {
    if (choice.disabled) { return; }
    setAgentChoice(choice.id);
    // Pre-fill flavor defaults (e.g. OpenRouter's API base URL) without
    // overwriting anything the operator already typed.
    if (choice.prefill) {
      setAgentDraft((current) => {
        const seed = { ...current };
        for (const [key, value] of Object.entries(choice.prefill)) {
          if (!(seed[key] || '').trim()) { seed[key] = value; }
        }
        return seed;
      });
    }
  };

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
    // clobber a value already present in settings.json. Includes the
    // optional workflow-state fields, not just the required subset.
    const payloadFields = {};
    for (const key of displayedKeys) {
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
    setStep(5);
  };

  const onSaveAgent = async () => {
    if (!agent) { return; }
    const updates = { KATO_AGENT_BACKEND: agent.backend };
    for (const key of agentGate.keys) {
      const value = (agentDraft[key] || '').trim();
      if (value) { updates[key] = value; }
    }
    // NOTE: updateAllSettings wraps the map in {updates: …} itself.
    const ok = await runSave(() => updateAllSettings(updates));
    if (!ok) { return; }
    toast.show({
      kind: 'success', title: 'AI agent saved', message: `${agent.label} selected.`,
    });
    await onRefreshStatus();
    setStep(4);
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
          displayedKeys={displayedKeys}
          requiredKeys={requiredKeys}
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
        <StepPickAgent
          agentChoice={agentChoice}
          onPick={onPickAgent}
          onBack={() => setStep(1)}
          onNext={() => setStep(3)}
        />
      )}

      {step === 3 && agent && (
        <StepAgentDetails
          agent={agent}
          keys={agentGate.keys}
          requiredSet={agentGate.required}
          draft={agentDraft}
          setDraft={setAgentDraft}
          serverHasValue={agentServerHas}
          canSave={agentGate.ok}
          saving={saving}
          onBack={() => setStep(2)}
          onSave={onSaveAgent}
        />
      )}

      {step === 4 && (
        <StepRepositories
          repoRoot={repoRoot}
          setRepoRoot={setRepoRoot}
          saving={saving}
          onBack={() => setStep(3)}
          onSave={onSaveRepo}
        />
      )}

      {step === 5 && (
        <StepFinish
          isConfigured={isConfigured}
          startFailed={Boolean(status?.setup_error)}
          missing={missing}
          onBack={() => setStep(4)}
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

// The shared fields block: one WizardField per key, requiredness from the
// (possibly dynamic) required set, labels humanized per platform prefix.
function WizardFieldList({ keys, requiredSet, labelPlatform, draft, setDraft, serverHasValue }) {
  return (
    <div className="setup-wizard-fields">
      {keys.map((key) => {
        const secret = isSecretKey(key);
        const alreadySet = serverHasValue(key);
        const optional = !requiredSet.has(key);
        return (
          <WizardField
            key={key}
            label={humanizeFieldKey(key, labelPlatform)}
            envKey={key}
            optional={optional}
            type={secret ? 'password' : 'text'}
            value={draft[key] || ''}
            onChange={(ev) => setDraft((current) => ({ ...current, [key]: ev.target.value }))}
            placeholder={
              secret && alreadySet
                ? '(already set — paste again to replace)'
                : fieldPlaceholder(key)
            }
          />
        );
      })}
    </div>
  );
}

// One labelled input row — the shared field shape for every wizard step.
// The env-var name is NOT printed; it lives in the ⓘ info tooltip.
function WizardField({ label, envKey, type = 'text', value, onChange, placeholder = '', optional = false }) {
  return (
    <label className="setup-wizard-field">
      <span className="setup-wizard-field-label">
        {label}
        {optional && <span className="setup-wizard-field-optional">optional</span>}
        <FieldInfoTip text={fieldInfo(envKey)} />
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
  platform, displayedKeys, requiredKeys, draft, setDraft,
  serverHasValue, canSave, saving, onBack, onSave,
}) {
  const label = TICKET_SYSTEMS.find((s) => s.id === platform)?.label || platform;
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Connect {label}</h3>
      <p className="setup-wizard-lead">
        Enter the details kato needs to read your tickets — the workflow
        fields are optional (kato has sensible defaults). Saved to
        {' '}<code>~/.kato/settings.json</code> — kato&apos;s only config
        file.
      </p>
      <WizardFieldList
        keys={displayedKeys}
        requiredSet={new Set(requiredKeys)}
        labelPlatform={platform}
        draft={draft}
        setDraft={setDraft}
        serverHasValue={serverHasValue}
      />
      <WizardSaveActions
        onBack={onBack}
        onSave={onSave}
        canSave={canSave}
        saving={saving}
      />
    </div>
  );
}

function StepPickAgent({ agentChoice, onPick, onBack, onNext }) {
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Which AI agent does the work?</h3>
      <p className="setup-wizard-lead">
        Pick the agent kato drives to implement your tickets. You can change
        this later in Settings.
      </p>
      <div className="setup-wizard-choices" role="radiogroup" aria-label="AI agent">
        {AGENT_CHOICES.map((choice) => (
          <button
            key={choice.id}
            type="button"
            role="radio"
            aria-checked={agentChoice === choice.id}
            disabled={choice.disabled}
            className={
              'setup-wizard-choice'
              + (agentChoice === choice.id ? ' is-selected' : '')
              + (choice.disabled ? ' is-disabled' : '')
            }
            onClick={() => onPick(choice)}
          >
            <span className="setup-wizard-choice-label">{choice.label}</span>
            <span className="setup-wizard-choice-blurb">{choice.blurb}</span>
          </button>
        ))}
      </div>
      <div className="setup-wizard-actions">
        <button type="button" className="setup-wizard-btn" onClick={onBack}>
          Back
        </button>
        <button
          type="button"
          className="setup-wizard-btn setup-wizard-btn--primary"
          disabled={!agentChoice}
          onClick={onNext}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function StepAgentDetails({
  agent, keys, requiredSet, draft, setDraft, serverHasValue, canSave,
  saving, onBack, onSave,
}) {
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Connect {agent.label}</h3>
      <p className="setup-wizard-lead">
        {agent.note
          || 'Enter the details kato needs to drive this agent — optional '
             + 'fields have sensible defaults.'}
      </p>
      {keys.length > 0 && (
        <WizardFieldList
          keys={keys}
          requiredSet={requiredSet}
          labelPlatform={agent.backend === 'claude' ? 'kato_claude' : 'openhands'}
          draft={draft}
          setDraft={setDraft}
          serverHasValue={serverHasValue}
        />
      )}
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
  const [browsing, setBrowsing] = useState(false);
  return (
    <div className="setup-wizard-body">
      <h3 className="setup-wizard-heading">Where are your repositories?</h3>
      <p className="setup-wizard-lead">
        The folder kato scans for <code>.git</code> repositories to clone and
        work in. An absolute path like <code>~/Projects</code>.
      </p>
      {/* The .setup-wizard-fields wrapper carries the bottom spacing that
          keeps the actions row from hugging the input (same as step 2). */}
      <div className="setup-wizard-fields">
        <div className="setup-wizard-field-row">
          <WizardField
            label="Repositories folder"
            envKey="REPOSITORY_ROOT_PATH"
            value={repoRoot}
            onChange={(ev) => setRepoRoot(ev.target.value)}
            placeholder="/Users/you/Projects"
          />
          <button
            type="button"
            className="setup-wizard-btn setup-wizard-btn--browse"
            onClick={() => setBrowsing((current) => !current)}
          >
            Browse…
          </button>
        </div>
        {browsing && (
          <FolderBrowser
            initialPath={repoRoot.trim() || '~'}
            onPick={(path) => {
              setRepoRoot(path);
              setBrowsing(false);
            }}
            onClose={() => setBrowsing(false)}
          />
        )}
      </div>
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
