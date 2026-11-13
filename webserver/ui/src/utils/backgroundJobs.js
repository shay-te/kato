// Which background jobs a Claude session is still waiting on — read from its
// own events. The SAME rule as the server's
// claude_core_lib/claude_core_lib/session/background_jobs.py (the session's
// ``is_working``); tests/test_background_jobs_parity.py runs both on the same
// cases, so the tab dot never disagrees with the server about it.
//
// A job is outstanding from its start (a ``run_in_background`` call, a
// Monitor, a Workflow, or the CLI's ``task_started`` with is_backgrounded)
// until it is REPORTED finished — in the same turn or a later one: the CLI's
// ``task_notification`` / ``task_updated`` (finished status) /
// ``background_tasks_changed`` list, or an older CLI's ``<task-notification>``
// user message. It used to be "the last turn used a background tool", which
// never ended for a job that finished inside its own turn.
//
// Pure and immutable: ``observeBackgroundEvent`` returns a new state.

export const BACKGROUND_TOOLS = new Set(['Monitor', 'Workflow']);
const WORKFLOW_TOOLS = new Set(['Workflow']);
export const FINISHED_STATUSES = new Set(
  ['completed', 'failed', 'killed', 'stopped', 'cancelled', 'error'],
);
const NOTIFICATION_PREFIX = '<task-notification>';
const TOOL_USE_ID = /<tool-use-id>\s*([^<\s]+)\s*<\/tool-use-id>/;

export function emptyBackgroundJobs() {
  // jobs: key (tool_use id, else task id) -> task id ('' = not known yet)
  return { jobs: {}, workflows: [], unnamed: 0 };
}

export function observeBackgroundEvent(state, raw) {
  const current = state || emptyBackgroundJobs();
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { return current; }
  const next = {
    jobs: { ...current.jobs },
    workflows: [...current.workflows],
    unnamed: current.unnamed,
  };
  if (raw.type === 'assistant') {
    for (const block of toolUses(raw)) {
      if (!startsBackgroundWork(block)) { continue; }
      let key = String(block.id || '');
      if (!key) {
        next.unnamed += 1;
        key = `unnamed-tool-use-${next.unnamed}`;
      }
      if (!owns(next.jobs, key)) { next.jobs[key] = ''; }
      if (WORKFLOW_TOOLS.has(String(block.name || ''))) { next.workflows.push(key); }
    }
  } else if (raw.type === 'system') {
    observeSystem(next, raw);
  } else if (raw.type === 'user') {
    observeNotificationText(next, raw);
  }
  delete next.jobs[''];
  return next;
}

export function outstandingBackgroundJobs(state) {
  return Object.keys((state && state.jobs) || {}).sort();
}

export function hasBackgroundJobs(state) {
  return outstandingBackgroundJobs(state).length > 0;
}

export function hasBackgroundWorkflow(state) {
  const jobs = (state && state.jobs) || {};
  return ((state && state.workflows) || []).some((key) => owns(jobs, key));
}

// The jobs still running after ``events`` (oldest first) — sorted keys.
export function outstandingFromEvents(events) {
  let state = emptyBackgroundJobs();
  for (const raw of events || []) { state = observeBackgroundEvent(state, raw); }
  return outstandingBackgroundJobs(state);
}

function observeSystem(next, raw) {
  const taskId = String(raw.task_id || '');
  const toolUseId = String(raw.tool_use_id || '');
  if (raw.subtype === 'task_started') {
    const key = toolUseId || taskId;
    if (raw.is_backgrounded === true || owns(next.jobs, key)) { next.jobs[key] = taskId; }
  } else if (raw.subtype === 'task_notification') {
    finish(next, { toolUseId, taskId });
  } else if (raw.subtype === 'task_updated') {
    const patch = raw.patch;
    const status = patch && typeof patch === 'object' && !Array.isArray(patch)
      ? String(patch.status || '') : '';
    if (FINISHED_STATUSES.has(status)) { finish(next, { taskId }); }
  } else if (raw.subtype === 'background_tasks_changed') {
    takeSnapshot(next, raw.tasks);
  }
}

// The CLI's own list: a job it knows the task of and no longer lists is over;
// one it lists that is not followed yet is followed now.
function takeSnapshot(next, tasks) {
  const running = new Set();
  if (Array.isArray(tasks)) {
    for (const task of tasks) {
      if (task && typeof task === 'object' && !Array.isArray(task)) {
        const id = String(task.task_id || '');
        if (id) { running.add(id); }
      }
    }
  }
  for (const [key, taskId] of Object.entries(next.jobs)) {
    if (taskId && !running.has(taskId)) { delete next.jobs[key]; }
  }
  const followed = new Set(Object.values(next.jobs));
  for (const taskId of running) {
    if (!followed.has(taskId)) { next.jobs[taskId] = taskId; }
  }
}

function observeNotificationText(next, raw) {
  const text = messageText(raw).trimStart();
  if (!text.startsWith(NOTIFICATION_PREFIX)) { return; }
  const named = TOOL_USE_ID.exec(text);
  if (named) {
    finish(next, { toolUseId: named[1] });
  } else {
    next.jobs = {}; // an older notification that names nothing: all reported
  }
}

function finish(next, { toolUseId = '', taskId = '' }) {
  for (const [key, knownTask] of Object.entries(next.jobs)) {
    if ((toolUseId && key === toolUseId) || (taskId && (taskId === key || taskId === knownTask))) {
      delete next.jobs[key];
    }
  }
}

function owns(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function toolUses(raw) {
  const message = raw.message;
  const content = message && typeof message === 'object' ? message.content : null;
  if (!Array.isArray(content)) { return []; }
  return content.filter((block) => block && typeof block === 'object' && block.type === 'tool_use');
}

function startsBackgroundWork(block) {
  if (BACKGROUND_TOOLS.has(String(block.name || ''))) { return true; }
  const input = block.input;
  return !!input && typeof input === 'object' && !Array.isArray(input)
    && input.run_in_background === true;
}

function messageText(raw) {
  const message = raw.message;
  const content = message && typeof message === 'object' ? message.content : null;
  if (typeof content === 'string') { return content; }
  if (Array.isArray(content)) {
    return content
      .filter((block) => block && typeof block === 'object' && block.type === 'text')
      .map((block) => String(block.text || ''))
      .join('');
  }
  return '';
}
