// Claude CLI slash commands that actually work through kato's transport.
//
// Kato talks to the CLI over `claude -p --output-format stream-json
// --input-format stream-json`, which is NOT the interactive REPL. Most slash
// commands are REPL-only and answer "<command> isn't available in this
// environment." — verified against CLI 2.1.179 by sending each one down this
// exact transport:
//
//   work        /compact  /clear  /context  /cost  /usage
//   refuse      /status /help /model /memory /agents /mcp /rewind
//               /permissions /output-style /config /export /doctor
//               /add-dir /vim /bug
//   unknown     /todos
//
// So this list is the working set, not the CLI's full menu. Offering the
// others would be a dropdown that mostly answers "not available here", which
// is worse than not offering them: it reads as kato being broken.
//
// Custom project commands (`.claude/commands/*.md`) still work — type them.

export const CLAUDE_COMMANDS = [
  {
    command: '/compact',
    label: 'Compact',
    description:
      'Summarise the conversation so far and continue in the SAME session. '
      + 'Use it when the context bar runs low — kato never does this on its own.',
  },
  {
    command: '/context',
    label: 'Context',
    description: 'Print a breakdown of what is filling the context window.',
  },
  {
    command: '/cost',
    label: 'Cost',
    description: 'Show token spend for this session.',
  },
  {
    command: '/usage',
    label: 'Usage',
    description: 'Show plan usage and limits for your account.',
  },
  {
    command: '/clear',
    label: 'Clear',
    description:
      'Wipe the conversation history and keep working in the same session. '
      + 'The agent forgets everything above this point — it cannot be undone.',
    destructive: true,
  },
];
