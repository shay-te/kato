// The ticket systems kato polls, by the id saved as KATO_ISSUE_PLATFORM — one
// list for every place that names them (setup wizard, Settings → Task
// provider, the empty task strip).

export const TICKET_SYSTEMS = [
  { id: 'youtrack', label: 'YouTrack', blurb: 'JetBrains YouTrack issues' },
  { id: 'jira', label: 'Jira', blurb: 'Atlassian Jira issues' },
  { id: 'github', label: 'GitHub Issues', blurb: 'Issues on GitHub' },
  { id: 'gitlab', label: 'GitLab Issues', blurb: 'Issues on GitLab' },
  { id: 'bitbucket', label: 'Bitbucket Issues', blurb: 'Issues on Bitbucket' },
];

// No tracker: tasks are created in kato's own "New task" tab.
export const NO_TRACKER = 'local';

// The ticket system's name ('' for none, or one kato does not know).
export function ticketSystemLabel(id) {
  return TICKET_SYSTEMS.find((system) => system.id === id)?.label || '';
}
