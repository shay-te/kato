# Issue Platforms — kato

Setup snippets for every supported issue/ticket platform.

## Supported Providers

The agent currently supports these issue trackers:

- YouTrack
- Jira
- GitHub Issues
- GitLab Issues
- Bitbucket Issues

The repository provider is inferred from the configured repository metadata, and the same task can span multiple repositories if the task text matches them.

## Third-Party Setup

Pick one issue platform with `KATO_ISSUE_PLATFORM`, then fill in the matching block below. Keep the other issue-platform blocks empty unless you are switching providers or using their repository API credentials for pull requests.

After editing `.env`, run:

```bash
kato doctor
```

## Where the API token comes from

Kato authenticates as one bot/user account: it reads the tickets assigned to that account, moves them between workflow states, comments on them, and — where the repository lives on the same host — pushes the branch and opens the pull request. It can do nothing that account cannot do. The token is stored on your machine in `~/.kato/settings.json` and sent only to that provider.

The first-run wizard shows these steps inline (Settings → credentials repeats them), but for reference:

| Platform | Where to create the token | Scopes / permissions |
|---|---|---|
| YouTrack | avatar → Profile → **Account Security** → Authentication → New token… — [docs](https://www.jetbrains.com/help/youtrack/devportal/Manage-Permanent-Token.html) | YouTrack scope |
| Jira | [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) → Security → Create and manage API tokens — [docs](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) | token + the account email (`JIRA_EMAIL`) |
| GitHub | avatar → Settings → **Developer settings** (bottom of the sidebar — there is no "Security" menu) → Personal access tokens → [Fine-grained tokens](https://github.com/settings/personal-access-tokens/new) — [docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) | Contents RW, Pull requests RW, Issues RW, Metadata R (classic token: `repo`) |
| GitLab | avatar → Edit profile → [Access tokens](https://gitlab.com/-/user_settings/personal_access_tokens) → Add new token — [docs](https://docs.gitlab.com/user/profile/personal_access_tokens/) | `api` |
| Bitbucket | [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) → Security → API tokens (Bitbucket Cloud authenticates through Atlassian) — [docs](https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/) | repositories / pull requests / issues RW, plus `BITBUCKET_USERNAME` and `BITBUCKET_API_EMAIL` |

## Give kato its own account (recommended)

Kato can run on your own account — the token is yours, so its comments and pull-request replies appear under your name. It works, but it leaves kato unable to answer one question reliably: **"did I write this comment, or did a human?"**

With a shared account the two are indistinguishable by author, so kato falls back to recognising its own writing by the *wording* of the comment. That is a guess, and it has failed in production more than once — each failure means kato reads its own comment as a new instruction, acts on it, comments again, and loops, emailing every watcher on every scan tick.

Give kato a separate user and the guess disappears: the comment author *is* the answer.

**Setup**

1. Create a new user on the ticket platform — e.g. `kato-bot` — and, if your repositories live elsewhere, on the git host too.
2. Grant it the same project access your own account has (read issues, comment, move states; push + open pull requests on the repo host).
3. Create the API token **while signed in as that user** and put it in kato's settings (`YOUTRACK_API_TOKEN`, `BITBUCKET_API_TOKEN`, …).
4. Point kato's scanning identity at it: `YOUTRACK_ASSIGNEE=kato-bot` (and `BITBUCKET_USERNAME` / `GITHUB_ASSIGNEE` / … to match the same account).
5. **Reassign the work.** Kato picks up tickets assigned to that account — anything still assigned to you will stop being picked up until you move it over.

**What changes once it's in place**

- Kato identifies its own ticket and pull-request comments by account, not by matching English sentences.
- `KATO_TASK_COMMENTS_REQUIRE_MENTION` becomes coherent: you `@kato-bot` to direct the agent, instead of @-mentioning yourself.
- Its comments are attributable in the ticket history — you can tell at a glance what the agent did versus what you did.

Kato still works without this, and every wording-based check remains as a fallback. This removes a class of failure rather than a specific bug.

## Setting Up YouTrack

Use this when tasks are coming from YouTrack:

```env
KATO_ISSUE_PLATFORM=youtrack
YOUTRACK_API_BASE_URL=https://your-company.youtrack.cloud
YOUTRACK_API_TOKEN=...
YOUTRACK_PROJECT=PROJ
YOUTRACK_ASSIGNEE=your-youtrack-login
YOUTRACK_ISSUE_STATES=Todo,Open
YOUTRACK_PROGRESS_STATE_FIELD=State
YOUTRACK_PROGRESS_STATE=In Progress
YOUTRACK_REVIEW_STATE_FIELD=State
YOUTRACK_REVIEW_STATE=To Verify
```

`YOUTRACK_ISSUE_STATES` is the queue Kato scans. The progress and review state settings tell Kato how to move the issue when work starts and when the pull request is ready.

## Setting Up Jira

Use this when tasks are coming from Jira:

```env
KATO_ISSUE_PLATFORM=jira
JIRA_API_BASE_URL=https://your-company.atlassian.net
JIRA_API_TOKEN=...
JIRA_EMAIL=you@example.com
JIRA_PROJECT=PROJ
JIRA_ASSIGNEE=assignee-account-id-or-username
JIRA_ISSUE_STATES=To Do,Open
JIRA_PROGRESS_STATE_FIELD=status
JIRA_PROGRESS_STATE=In Progress
JIRA_REVIEW_STATE_FIELD=status
JIRA_REVIEW_STATE=In Review
```

`JIRA_API_TOKEN` is the API token. Keep `JIRA_EMAIL` set for Atlassian authentication flows that need the account email.

## Setting Up GitHub Issues

Use this when tasks are coming from GitHub Issues:

```env
KATO_ISSUE_PLATFORM=github
GITHUB_API_BASE_URL=https://api.github.com
GITHUB_API_TOKEN=...
GITHUB_OWNER=owner-or-org
GITHUB_REPO=repo-name
GITHUB_ASSIGNEE=assignee-login
GITHUB_ISSUE_STATES=open
GITHUB_PROGRESS_STATE_FIELD=labels
GITHUB_PROGRESS_STATE=In Progress
GITHUB_REVIEW_STATE_FIELD=labels
GITHUB_REVIEW_STATE=In Review
```

`GITHUB_API_TOKEN` is also used for GitHub git push and pull request creation when discovered repositories live on GitHub.

## Setting Up GitLab Issues

Use this when tasks are coming from GitLab Issues:

```env
KATO_ISSUE_PLATFORM=gitlab
GITLAB_API_BASE_URL=https://gitlab.com/api/v4
GITLAB_API_TOKEN=...
GITLAB_PROJECT=group/project
GITLAB_ASSIGNEE=assignee-username
GITLAB_ISSUE_STATES=opened
GITLAB_PROGRESS_STATE_FIELD=labels
GITLAB_PROGRESS_STATE=In Progress
GITLAB_REVIEW_STATE_FIELD=labels
GITLAB_REVIEW_STATE=In Review
```

`GITLAB_API_TOKEN` is also used for GitLab git push and merge request creation when discovered repositories live on GitLab.

## Setting Up Bitbucket Issues

Use this when tasks are coming from Bitbucket Issues:

```env
KATO_ISSUE_PLATFORM=bitbucket
BITBUCKET_API_BASE_URL=https://api.bitbucket.org/2.0
BITBUCKET_API_TOKEN=...
BITBUCKET_USERNAME=bitbucket-username
BITBUCKET_API_EMAIL=you@example.com
BITBUCKET_WORKSPACE=workspace
BITBUCKET_REPO_SLUG=repo-slug
BITBUCKET_ASSIGNEE=assignee-username
BITBUCKET_ISSUE_STATES=new,open
BITBUCKET_PROGRESS_STATE_FIELD=state
BITBUCKET_PROGRESS_STATE=open
BITBUCKET_REVIEW_STATE_FIELD=state
BITBUCKET_REVIEW_STATE=resolved
```

`BITBUCKET_API_TOKEN` is used for Bitbucket git auth and REST API calls. `BITBUCKET_API_EMAIL` is required for Bitbucket pull request API auth.
