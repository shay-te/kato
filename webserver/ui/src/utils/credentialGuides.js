// "Why does kato need this, and where do I get it?" — per-provider
// credential guidance for every screen that asks for a token/key.
//
// First-install feedback that produced this file: an operator who does not
// mint API tokens for a living could not find GitHub's — "there is no
// SECURITY menu" — and they were right: GitHub keeps personal access tokens
// under *Developer settings*, at the very bottom of the settings sidebar.
// The old generic ⓘ text ("create one in your account's security settings")
// pointed at a menu that does not exist on GitHub, GitLab or Bitbucket.
//
// So every provider we ask a credential for carries: why kato needs it, the
// exact menu path, the permissions/scopes to grant, a deep link to the
// create page, and a link to the provider's own documentation.
//
// Consumed by <CredentialGuide> (first-run wizard + the Settings
// credentials panels — the question outlives first run) and by
// fieldHelp.js, which uses ``location`` for the token field's ⓘ text so
// there is ONE source of truth for where a token is created.

// Keys are the provider/agent ids the wizard and the settings panels
// already use (``/api/task-providers`` names + AGENT_CHOICES ids).
export const CREDENTIAL_GUIDES = {
  youtrack: {
    provider: 'YouTrack',
    credential: 'permanent token',
    why: 'Kato signs in to YouTrack as this account to read the tickets '
      + 'assigned to it, move them between workflow states, and post its '
      + 'progress comments. It can do nothing this account cannot do.',
    location: 'YouTrack → your avatar (top right) → Profile → Account Security → Authentication',
    steps: [
      'Sign in to YouTrack as the account kato should act as (the same login you put in the Assignee field).',
      'Click your avatar (top right) → Profile.',
      'Open the Account Security tab → Authentication.',
      'Click New token…, name it (for example "kato"), and keep the YouTrack scope.',
      'Copy the token — YouTrack shows it only once — and paste it above.',
    ],
    docsUrl: 'https://www.jetbrains.com/help/youtrack/devportal/Manage-Permanent-Token.html',
    docsLabel: 'JetBrains: create a permanent token',
  },

  jira: {
    provider: 'Jira',
    credential: 'API token',
    why: 'Kato calls the Jira API as this account to read issues assigned '
      + 'to it, transition them (In Progress → In Review), and comment on '
      + 'what it did. Jira also needs the account email alongside the token.',
    location: 'Atlassian account (id.atlassian.com) → Security → Create and manage API tokens',
    steps: [
      'Sign in at id.atlassian.com with the Atlassian account kato should act as.',
      'Open Security → Create and manage API tokens.',
      'Click Create API token and name it (for example "kato").',
      'Copy the token — Atlassian shows it only once — and paste it above.',
      'Put that same account\'s email address in the Email field; Jira authenticates with email + token together.',
    ],
    createUrl: 'https://id.atlassian.com/manage-profile/security/api-tokens',
    createLabel: 'Create an Atlassian API token',
    docsUrl: 'https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/',
    docsLabel: 'Atlassian: manage API tokens',
  },

  github: {
    provider: 'GitHub',
    credential: 'personal access token',
    why: 'Kato uses this token to read the issues assigned to it and — for '
      + 'repositories hosted on GitHub — to push its branch and open the '
      + 'pull request. Without it kato can see nothing and push nothing.',
    location: 'GitHub → your avatar → Settings → Developer settings → Personal access tokens',
    steps: [
      'Sign in to github.com and click your avatar (top right) → Settings.',
      'Scroll to the very bottom of the left sidebar → Developer settings. (There is no "Security" menu — this is the entry most people miss.)',
      'Personal access tokens → Fine-grained tokens → Generate new token.',
      'Set Resource owner to the user or organization that owns your repositories, then select the repositories kato works on.',
      'Under Repository permissions grant: Contents = Read and write, Pull requests = Read and write, Issues = Read and write, Metadata = Read-only.',
      'Click Generate token and copy it — GitHub shows it only once.',
    ],
    note: 'A classic token works too — it needs the repo scope.',
    createUrl: 'https://github.com/settings/personal-access-tokens/new',
    createLabel: 'Create a token on GitHub',
    docsUrl: 'https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens',
    docsLabel: 'GitHub: managing personal access tokens',
  },

  gitlab: {
    provider: 'GitLab',
    credential: 'personal access token',
    why: 'Kato uses this token to read the issues assigned to it and — for '
      + 'repositories hosted on GitLab — to push its branch and open the '
      + 'merge request.',
    location: 'GitLab → your avatar → Edit profile → Access tokens',
    steps: [
      'Sign in to GitLab and click your avatar (top right) → Edit profile.',
      'Open Access tokens in the left sidebar → Add new token.',
      'Name it (for example "kato") and set an expiry date.',
      'Tick the api scope — it covers issues, merge requests and git over HTTPS in one.',
      'Click Create personal access token and copy it — GitLab shows it only once.',
    ],
    note: 'Self-managed GitLab: the same path on your own instance URL.',
    createUrl: 'https://gitlab.com/-/user_settings/personal_access_tokens',
    createLabel: 'Create a token on GitLab',
    docsUrl: 'https://docs.gitlab.com/user/profile/personal_access_tokens/',
    docsLabel: 'GitLab: personal access tokens',
  },

  bitbucket: {
    provider: 'Bitbucket',
    credential: 'API token',
    why: 'Kato uses this token to read the issues and pull-request review '
      + 'comments addressed to it, and to push its branch and open the pull '
      + 'request. Bitbucket Cloud authenticates through your Atlassian '
      + 'account, so it needs the username and the account email too.',
    location: 'Atlassian account (id.atlassian.com) → Security → Create and manage API tokens',
    steps: [
      'Sign in at id.atlassian.com with the Atlassian account behind your Bitbucket user.',
      'Open Security → Create and manage API tokens.',
      'Click Create API token with scopes, pick Bitbucket, and grant read + write on repositories, pull requests and issues.',
      'Copy the token — Atlassian shows it only once — and paste it above.',
      'Fill Username with your Bitbucket username and API email with that Atlassian account\'s email — Bitbucket needs both alongside the token.',
    ],
    createUrl: 'https://id.atlassian.com/manage-profile/security/api-tokens',
    createLabel: 'Create an Atlassian API token',
    docsUrl: 'https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/',
    docsLabel: 'Bitbucket: using API tokens',
  },

  claude: {
    provider: 'Claude',
    credential: 'login',
    // Nothing is pasted here — say so, so nobody hunts for a key that the
    // CLI already holds.
    storesSecret: false,
    why: 'Nothing to paste here: kato drives the Claude Code CLI installed '
      + 'on this machine and reuses the credentials that CLI already holds. '
      + 'That is what pays for the model that writes your code.',
    location: 'Your terminal — the CLI stores the credentials, kato never sees them',
    steps: [
      'Install the Claude Code CLI on this machine if you have not already.',
      'Run `claude login` once in a terminal and finish the browser sign-in (a Claude Pro/Max subscription covers it).',
      'Alternatively, set an ANTHROPIC_API_KEY from the Anthropic Console if you pay per token instead.',
      'Check it worked: `claude --version` should print a version.',
    ],
    createUrl: 'https://console.anthropic.com/settings/keys',
    createLabel: 'Anthropic Console: API keys',
    docsUrl: 'https://docs.claude.com/en/docs/claude-code/overview',
    docsLabel: 'Claude Code: install & sign in',
  },

  openhands: {
    provider: 'OpenHands',
    credential: 'server key',
    why: 'The OpenHands key authenticates kato with your OpenHands server, '
      + 'and the LLM key is what the agent uses to call the model that '
      + 'writes the code. The OH secret key is a random string OpenHands '
      + 'uses to encrypt stored secrets.',
    location: 'Your OpenHands server\'s own settings page',
    steps: [
      'Point Base URL at the OpenHands server you run (for example http://localhost:3000).',
      'Take the API key from that server\'s settings — it is issued by OpenHands, not by kato.',
      'For the OH secret key generate any stable random string once and keep it: `openssl rand -hex 32`.',
      'The LLM API key comes from whoever serves your model (Anthropic Console, OpenRouter, Azure, …).',
    ],
    docsUrl: 'https://docs.all-hands.dev/',
    docsLabel: 'OpenHands documentation',
  },

  openrouter: {
    provider: 'OpenRouter',
    credential: 'API key',
    why: 'The OpenRouter key is what pays for — and gives access to — the '
      + 'model the agent runs on. Kato sends it to OpenRouter with each '
      + 'model call and nowhere else.',
    location: 'openrouter.ai → your avatar → Keys',
    steps: [
      'Sign in at openrouter.ai and open Keys from the avatar menu.',
      'Click Create key, name it (for example "kato"), and copy the value.',
      'Paste it into the LLM API key field above.',
      'Leave the LLM base URL as https://openrouter.ai/api/v1 and set the model to openrouter/<vendor>/<model>.',
    ],
    createUrl: 'https://openrouter.ai/keys',
    createLabel: 'Create an OpenRouter key',
    docsUrl: 'https://openrouter.ai/docs/api-keys',
    docsLabel: 'OpenRouter: API keys',
  },

  bedrock: {
    provider: 'AWS Bedrock',
    credential: 'AWS credentials',
    why: 'Bedrock models are billed through AWS, so instead of an LLM key '
      + 'the agent authenticates with your AWS credentials when it invokes '
      + 'the model.',
    location: 'AWS Console → Bedrock (request model access) → IAM (create the credentials)',
    steps: [
      'In the AWS Console open Bedrock → Model access and request access to the model you want.',
      'Create either a Bedrock API key (bearer token) or an IAM user/role with the bedrock:InvokeModel permission.',
      'For the bearer token: paste it into AWS bearer token bedrock and leave the other three fields blank.',
      'For IAM credentials: fill access key id, secret access key AND region — all three together.',
    ],
    docsUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/api-setup.html',
    docsLabel: 'AWS: Bedrock API credentials',
  },
};

// The guide for a provider / agent id ('github', 'openrouter', …).
// Unknown ids return null and every consumer renders nothing.
export function credentialGuideFor(providerId) {
  const id = String(providerId || '').trim().toLowerCase();
  return CREDENTIAL_GUIDES[id] || null;
}

// The guide a settings key belongs to, from its platform prefix —
// 'GITHUB_API_TOKEN' → the GitHub guide. Used by fieldHelp so the ⓘ text
// and the guide card can never drift apart.
export function credentialGuideForKey(key) {
  return credentialGuideFor(String(key || '').split('_')[0]);
}

// One-line "where this is created" for the ⓘ tooltip; '' when unknown.
export function credentialLocationForKey(key) {
  return credentialGuideForKey(key)?.location || '';
}
