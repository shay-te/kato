// Shared field metadata for every setup/settings input across the system:
// friendly labels, example-value placeholders, and the ⓘ info text. The raw
// environment-variable name is NEVER printed next to a label — it lives on
// the last line of the info text (FieldInfoTip) instead.

import { credentialLocationForKey } from './credentialGuides.js';

// "YOUTRACK_API_BASE_URL" → "API base URL" (drop the platform prefix, keep
// well-known acronyms readable) so a first-comer sees friendly labels.
export function humanizeFieldKey(key, platform) {
  const prefix = `${String(platform).toUpperCase()}_`;
  let text = String(key).startsWith(prefix) ? key.slice(prefix.length) : key;
  text = text.replace(/_/g, ' ').toLowerCase();
  text = text.replace(/\bapi\b/g, 'API').replace(/\burl\b/g, 'URL');
  text = text.replace(/\bllm\b/g, 'LLM').replace(/\boh\b/g, 'OH');
  text = text.replace(/\baws\b/g, 'AWS');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const PLATFORM_LABELS = {
  YOUTRACK: 'YouTrack',
  JIRA: 'Jira',
  GITHUB: 'GitHub',
  GITLAB: 'GitLab',
  BITBUCKET: 'Bitbucket',
};

const PLATFORM_URL_EXAMPLES = {
  YOUTRACK: 'https://yourcompany.youtrack.cloud/api',
  JIRA: 'https://your-domain.atlassian.net',
  GITHUB: 'https://api.github.com',
  GITLAB: 'https://gitlab.com/api/v4',
  BITBUCKET: 'https://api.bitbucket.org/2.0',
};

function platformOf(key) {
  const head = String(key).split('_')[0];
  return PLATFORM_LABELS[head] ? head : '';
}

function serviceName(key) {
  const head = platformOf(key);
  return head ? PLATFORM_LABELS[head] : 'the service';
}

// Exact-key rules for fields whose meaning a suffix can't capture.
// [placeholder, info]
const EXACT_RULES = {
  OPENHANDS_BASE_URL: ['http://localhost:3000',
    'URL of your running OpenHands server.'],
  OPENHANDS_API_KEY: ['paste your OpenHands API key',
    'API key for authenticating with the OpenHands server.'],
  OH_SECRET_KEY: ['paste a stable random secret',
    'Stable random secret OpenHands uses for secret persistence — generate once and keep it.'],
  OPENHANDS_LLM_MODEL: ['openrouter/openai/gpt-4o',
    'LiteLLM-style model id the agent runs with, e.g. anthropic/claude-sonnet-4-5, openrouter/openai/gpt-4o, or bedrock/… .'],
  OPENHANDS_LLM_API_KEY: ['paste your LLM provider API key',
    'API key for the LLM provider (Anthropic, OpenRouter, Azure, …). Not needed for AWS-Bedrock models using AWS credentials.'],
  OPENHANDS_LLM_BASE_URL: ['https://openrouter.ai/api/v1',
    'Override of the default LLM API endpoint. Required for OpenRouter; usually blank otherwise.'],
  KATO_CLAUDE_MODEL: ['opus',
    'Optional model alias (opus / sonnet / haiku) — aliases track the latest version. Blank uses the CLI default.'],
  AWS_BEARER_TOKEN_BEDROCK: ['paste your Bedrock bearer token',
    'For bedrock/… models: either this bearer token OR the access-key trio (key id + secret + region, all three together).'],
  AWS_ACCESS_KEY_ID: ['AKIA…',
    'For bedrock/… models without a bearer token: AWS access key id (needs secret + region too).'],
  AWS_SECRET_ACCESS_KEY: ['paste your AWS secret access key',
    'For bedrock/… models without a bearer token: AWS secret access key (needs key id + region too).'],
  AWS_REGION_NAME: ['us-east-1',
    'For bedrock/… models without a bearer token: AWS region of the Bedrock endpoint.'],
};

// Longest-suffix-first rules: [suffix, placeholder, info sentence(s)].
// Suffix-driven so one table covers every platform's prefixed keys plus
// the generic schema keys that share the same endings.
const SUFFIX_RULES = [
  ['API_BASE_URL', (key) => PLATFORM_URL_EXAMPLES[platformOf(key)] || 'https://…',
    (key) => `Base URL of the ${serviceName(key)} API kato talks to.`],
  ['BASE_URL', () => 'https://…',
    (key) => `Base URL of the ${serviceName(key)} API kato talks to.`],
  // The menu path comes from credentialGuides so the tooltip and the
  // guide card can't drift. (The old text said "your account's security
  // settings" — a menu that exists on none of these providers, which is
  // exactly what stalled the first install.)
  ['API_TOKEN', () => 'paste your API token',
    (key) => `API token kato authenticates with. Create one at: ${
      credentialLocationForKey(key) || `your ${serviceName(key)} account settings`}.`],
  ['API_EMAIL', () => 'bot@company.com',
    () => 'Email address of the account the API token belongs to.'],
  ['EMAIL', () => 'bot@company.com',
    () => 'Email address of the account kato acts as.'],
  ['USERNAME', () => 'kato-bot',
    () => 'Username of the account the API token belongs to.'],
  ['PROGRESS_STATE_FIELD', () => 'State',
    () => 'Name of the workflow field kato updates when it starts working. Leave blank to use kato’s default.'],
  ['PROGRESS_STATE', () => 'In Progress',
    () => 'Workflow state kato moves a ticket to while working on it. Leave blank to use kato’s default.'],
  ['REVIEW_STATE_FIELD', () => 'State',
    () => 'Name of the workflow field kato updates when work is ready for review. Leave blank to use kato’s default.'],
  ['REVIEW_STATE', () => 'To Verify',
    () => 'Workflow state kato moves a ticket to after opening the pull request. Leave blank to use kato’s default.'],
  ['ISSUE_STATES', () => 'Open,To Do',
    () => 'Comma-separated list of states kato treats as ready to pick up. Leave blank to use kato’s default.'],
  ['REPO_SLUG', () => 'my-repo',
    () => 'Repository slug (the short name in the repository URL).'],
  ['WORKSPACE', () => 'my-workspace',
    () => 'Workspace (team) the repository lives in.'],
  ['PROJECT', (key) => (platformOf(key) === 'GITLAB' ? 'group/project' : 'PROJ'),
    (key) => `${serviceName(key)} project kato scans for assigned issues.`],
  ['ASSIGNEE', () => 'kato',
    () => 'Issues assigned to this user are the ones kato picks up.'],
  ['OWNER', () => 'my-org',
    () => 'Organization or user that owns the repository.'],
  ['REPO', () => 'my-repo',
    () => 'Repository name.'],
  ['ROOT_PATH', () => '/Users/you/Projects',
    () => 'Absolute folder kato walks for .git repositories to auto-discover. ~ is expanded on save.'],
  ['API_KEY', () => 'paste your API key',
    () => 'API key kato authenticates with.'],
  ['TOKEN', () => 'paste your token',
    () => 'Token kato authenticates with.'],
  ['SECRET_KEY', () => 'paste your secret key',
    () => 'Secret key for the service.'],
  ['MODEL', () => 'gpt-4',
    () => 'Model identifier the agent runs with.'],
  ['PATH', () => '/absolute/path',
    () => 'Absolute filesystem path.'],
];

function ruleFor(key) {
  const upper = String(key).toUpperCase();
  return SUFFIX_RULES.find(([suffix]) => upper.endsWith(suffix)) || null;
}

// Example-value placeholder for the input. '' when no rule matches (the
// caller may have a better one, e.g. from the settings schema).
export function fieldPlaceholder(key) {
  const exact = EXACT_RULES[String(key).toUpperCase()];
  if (exact) { return exact[0]; }
  const rule = ruleFor(key);
  return rule ? rule[1](key) : '';
}

// The ⓘ tooltip body: what the field is, then — on its own line — the
// environment-variable name (its ONLY home in the UI).
export function fieldInfo(key, extra = '') {
  const exact = EXACT_RULES[String(key).toUpperCase()];
  const rule = exact ? null : ruleFor(key);
  const parts = [];
  if (exact) { parts.push(exact[1]); }
  if (rule) { parts.push(rule[2](key)); }
  if (extra) { parts.push(extra); }
  parts.push(`Environment variable: ${key}`);
  return parts.join('\n\n');
}
