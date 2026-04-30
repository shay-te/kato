import { NOTIFICATION_KIND } from '../constants/notificationKind.js';

const PATTERNS = [
  {
    re: /^task (\S+) tagged kato:wait-planning/,
    build: (m) => ({
      title: 'Planning chat ready', body: m[1], taskId: m[1], kind: NOTIFICATION_KIND.STARTED,
    }),
  },
  {
    re: /^Mission (\S+): starting mission(?:: (.+))?/,
    build: (m) => ({
      title: 'Task started',
      body: m[2] ? `${m[1]}: ${m[2]}` : m[1],
      taskId: m[1],
      kind: NOTIFICATION_KIND.STARTED,
    }),
  },
  {
    re: /^Mission (\S+): workflow completed successfully/,
    build: (m) => ({
      title: 'Task completed', body: m[1], taskId: m[1], kind: NOTIFICATION_KIND.COMPLETED,
    }),
  },
  {
    re: /^task (\S+): claude is asking permission to run (\S+)/,
    build: (m) => ({
      title: 'Approval needed',
      body: `${m[1]} → ${m[2]}`,
      taskId: m[1],
      kind: NOTIFICATION_KIND.ATTENTION,
    }),
  },
  {
    re: /^task (\S+): claude turn ended \(error\)/,
    build: (m) => ({
      title: 'Turn failed', body: m[1], taskId: m[1], kind: NOTIFICATION_KIND.ERROR,
    }),
  },
];

export function classifyStatusEntry(entry) {
  const message = (entry && entry.message) || '';
  for (const { re, build } of PATTERNS) {
    const match = message.match(re);
    if (match) { return build(match); }
  }
  return null;
}
