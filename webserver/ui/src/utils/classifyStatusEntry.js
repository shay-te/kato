import { NOTIFICATION_KIND } from '../constants/notificationKind.js';

// How each way a review loop can end is announced.
const REVIEW_LOOP_OUTCOMES = {
  clean: { title: 'Review loop: clean', kind: NOTIFICATION_KIND.COMPLETED },
  max_rounds: { title: 'Review loop: issues left after the last round', kind: NOTIFICATION_KIND.ATTENTION },
  stuck: { title: 'Review loop: stuck on the same issues', kind: NOTIFICATION_KIND.ATTENTION },
  failed: { title: 'Review loop failed', kind: NOTIFICATION_KIND.ERROR },
  stopped: { title: 'Review loop stopped', kind: NOTIFICATION_KIND.STATUS_CHANGE },
  interrupted: { title: 'Review loop interrupted', kind: NOTIFICATION_KIND.STATUS_CHANGE },
};

const PATTERNS = [
  {
    re: /^task (\S+) tagged kato:wait-planning/,
    build: (m) => {
      return {
        title: 'Planning chat ready',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.STARTED,
      };
    },
  },
  {
    // Same hold, different contract: the agent is parked and will edit
    // directly (no plan) once the operator hands it the go-ahead.
    re: /^task (\S+) tagged kato:wait-editing/,
    build: (m) => {
      return {
        title: 'Waiting for your go-ahead',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.STARTED,
      };
    },
  },
  {
    re: /^Mission (\S+): starting mission(?:: (.+))?/,
    build: (m) => {
      const body = m[2] ? `${m[1]}: ${m[2]}` : m[1];
      return {
        title: 'Task started',
        body,
        taskId: m[1],
        kind: NOTIFICATION_KIND.STARTED,
      };
    },
  },
  {
    re: /^Mission (\S+): moved issue to in progress/,
    build: (m) => {
      return {
        title: 'Task → In Progress',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.STATUS_CHANGE,
      };
    },
  },
  {
    re: /^Mission (\S+): moved issue to review state/,
    build: (m) => {
      return {
        title: 'Task → Review',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.STATUS_CHANGE,
      };
    },
  },
  {
    re: /^task (\S+) implementation complete; awaiting push approval/,
    build: (m) => {
      return {
        title: 'Awaiting push approval',
        body: `${m[1]}: click "Approve push" to push and open the PR`,
        taskId: m[1],
        kind: NOTIFICATION_KIND.ATTENTION,
      };
    },
  },
  {
    re: /^Mission (\S+): workflow completed successfully/,
    build: (m) => {
      return {
        title: 'Task completed',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.COMPLETED,
      };
    },
  },
  {
    re: /^task (\S+): claude is asking permission to run (\S+)/,
    build: (m) => {
      return {
        title: 'Approval needed',
        body: `${m[1]} → ${m[2]}`,
        taskId: m[1],
        // The tool name (e.g. ``Bash``/``WebFetch``) so the notification
        // router can recall a saved decision and stay silent for an ask
        // that auto-resolves. Only the permission pattern sets this.
        permissionTool: m[2],
        kind: NOTIFICATION_KIND.ATTENTION,
      };
    },
  },
  {
    re: /^task (\S+): claude turn ended \(error\)/,
    build: (m) => {
      return {
        title: 'Turn failed',
        body: m[1],
        taskId: m[1],
        kind: NOTIFICATION_KIND.ERROR,
      };
    },
  },
  {
    // A repository clone failed during workspace provisioning. Emitted by
    // provision_task_workspace_clones before it re-raises.
    //
    // This needs to be a NOTIFICATION and not just a preflight-log line: the
    // preflight log is replayed into a single task's stream, so it only ever
    // reaches someone already looking at that chat — and a clone failure is
    // precisely the case where that chat has no files to look at. Silent
    // degradation here read as "kato is buggy": an empty Files pane with
    // nothing anywhere saying a clone had failed.
    //
    // Groups: task, reason. The reason is a git error and can be long, so
    // it is trimmed for the notification body — the full text stays in the
    // status feed and the preflight log.
    re: /^Mission (\S+): repository clone failed: (.+)/,
    build: (m) => {
      const reason = m[2].length > 120 ? `${m[2].slice(0, 117)}…` : m[2];
      return {
        title: 'Repository clone failed',
        body: `${m[1]}: ${reason}`,
        taskId: m[1],
        kind: NOTIFICATION_KIND.ERROR,
      };
    },
  },
  {
    // "Update source" (push + shift local clones to the task branch) finished.
    // Emitted by AgentService.update_source_for_task. Groups: task,
    // updated-count, skipped-count, failed-count.
    re: /^Mission (\S+): source update finished \((\d+) updated, (\d+) skipped, (\d+) failed\)/,
    build: (m) => {
      const updated = Number(m[2]);
      const failed = Number(m[4]);
      const failedSuffix = failed > 0 ? `, ${failed} failed` : '';
      return {
        title: failed > 0 ? 'Source update finished (with errors)' : 'Source updated',
        body: `${m[1]}: ${updated} repo(s) updated${failedSuffix}`,
        taskId: m[1],
        kind: NOTIFICATION_KIND.SOURCE_UPDATE,
      };
    },
  },
  {
    // A review loop ended — announced even when the operator is on another
    // task (the task on screen also gets a toast from useReviewLoop).
    // Emitted by kato's review-loop logger (review_loop_adapters.py). Groups:
    // task, final status, rounds, reason.
    re: /^Mission (\S+): review loop finished \((\w+)\) after (\d+) round\(s\): (.*)/,
    build: (m) => {
      const outcome = REVIEW_LOOP_OUTCOMES[m[2]] || REVIEW_LOOP_OUTCOMES.stopped;
      return {
        title: outcome.title,
        body: `${m[1]}: ${m[4]}`,
        taskId: m[1],
        kind: outcome.kind,
      };
    },
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
