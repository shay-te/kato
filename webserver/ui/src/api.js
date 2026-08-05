import { AGENT_SESSION_ID } from './constants/sessionFields.js';

async function fetchJson(url, { timeoutMs = 0 } = {}) {
  // Optional hard timeout: without it a hung endpoint keeps the promise
  // pending forever, and any caller keyed on "have I loaded yet?" (e.g. the
  // publish-state poll behind the git buttons) gets stuck showing a loading
  // state indefinitely instead of surfacing an error and retrying.
  const controller = timeoutMs > 0 && typeof AbortController !== 'undefined'
    ? new AbortController() : null;
  const timer = controller && typeof window !== 'undefined'
    ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(url, {
      cache: 'no-store',
      ...(controller ? { signal: controller.signal } : {}),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (body && body.error) { message = body.error; }
      } catch (_) { /* fall through with status text */ }
      throw new Error(message);
    }
    return await response.json();
  } finally {
    if (timer) { window.clearTimeout(timer); }
  }
}

// Standard envelope: ``{ ok, status, body }`` on a completed request,
// ``{ ok: false, error }`` when fetch itself throws (network down, etc.).
async function requestEnvelope(url, init) {
  try {
    const response = await fetch(url, init);
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// POST/DELETE/etc. helper: adds the JSON content-type header +
// serialized body, then delegates to requestEnvelope.
function postEnvelope(url, jsonBody, method = 'POST') {
  return requestEnvelope(url, {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(jsonBody),
  });
}

// Drain a completed response into the "strict" envelope shape:
// ``{ ok: true, body }`` on a 2xx, ``{ ok: false, status, error }``
// on a non-2xx. Shared by ``requestEnvelopeStrict`` and the bespoke
// ``fetchAllAssignedTasks`` (which can't use the wrapper directly —
// it owns the fetch so it can bound it with an AbortController).
async function strictEnvelopeFrom(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    return { ok: false, status: response.status, error: body.error || response.statusText };
  }
  return { ok: true, body };
}

// "Strict" envelope used by the list/fetch endpoints: ``{ ok: true, body }``
// on success, ``{ ok: false, status, error }`` on a non-2xx response, and
// ``{ ok: false, error }`` when fetch throws.
async function requestEnvelopeStrict(url, init) {
  try {
    const response = await fetch(url, init);
    return strictEnvelopeFrom(response);
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchSessionList() {
  return fetchJson('/api/sessions');
}

// Every unanswered permission ask across ALL live sessions (not just the
// focused tab's SSE stream) — so the modal can pop no matter which task is
// in view. Returns ``{ pending: [{ task_id, type, request_id, request, … }] }``.
export function fetchPendingPermissions() {
  return fetchJson('/api/permissions/pending');
}

// Backend-owned remembered "Allow always" / "Deny always" decisions —
// the browser holds no copy of its own (kato_core_lib/helpers/
// tool_decision_store.py is the sole source of truth; the server
// auto-resolves a matching pending ask before it's ever surfaced here).
// Returns ``{ decisions: [{tool_name, command_signature, allow}, …] }``.
export function fetchToolDecisions() {
  return requestEnvelope('/api/tool-decisions');
}

export function forgetToolDecision(toolName, commandSignature = '') {
  return postEnvelope('/api/tool-decisions/forget', {
    tool_name: toolName,
    command_signature: commandSignature,
  });
}

export function setToolDecision(toolName, commandSignature, allow) {
  return postEnvelope('/api/tool-decisions/set', {
    tool_name: toolName,
    command_signature: commandSignature,
    allow: !!allow,
  });
}

export function clearToolDecisions() {
  return postEnvelope('/api/tool-decisions/clear', {});
}

export function fetchSafetyState() {
  return fetchJson('/api/safety');
}

// Setup mode — did kato boot unconfigured (``setup_mode``), and is the config
// complete right now (``needs_config`` / ``missing``). Drives the first-run
// wizard gate (see useConfigStatus + SetupModeGate). Evaluated live across
// env > settings.json, so saving in the wizard clears items here
// without a restart.
export function fetchConfigStatus() {
  return fetchJson('/api/config-status');
}

// Folder-picker listing for the "Browse…" button (wizard + Settings →
// Repositories). Directory names only; the server never lists files.
export function fetchDirectoryListing(path) {
  return fetchJson(`/api/fs/dirs?path=${encodeURIComponent(path || '~')}`);
}

export function fetchAgentVersion(force = false) {
  // ``force`` re-probes the host CLI server-side (banner/upgrade button reflect
  // a CLI or settings change with no kato restart).
  return fetchJson(force ? '/api/agent-version?refresh=1' : '/api/agent-version');
}

export function upgradeAgentCli() {
  // STARTS the upgrade and returns immediately; ``body`` is the first progress
  // snapshot. Poll fetchAgentUpgradeStatus for the bar + live output.
  return postEnvelope('/api/agent-version/upgrade', {});
}

export function fetchAgentUpgradeStatus() {
  // Progress snapshot of the in-flight (or last) upgrade:
  // { state, percent, step, command, lines, ok, message, version_before,
  //   version_after }. The job lives server-side, so this re-attaches after a
  // reload instead of losing a run that's still going.
  return fetchJson('/api/agent-version/upgrade');
}

// Settings drawer — currently exposes ``repository_root_path`` only.
// The shape ``{ ok, body }`` matches what fetchTaskComments returns
// so the drawer doesn't need a special-cased fetch wrapper.
export function fetchSettings() {
  return requestEnvelope('/api/settings');
}

export function updateSettings(payload) {
  return postEnvelope('/api/settings', payload || {});
}

// Repository approvals (used to live behind ``./kato approve-repo``).
export function fetchRepositoryApprovals() {
  return requestEnvelope('/api/repository-approvals');
}

export function updateRepositoryApprovals(payload) {
  return postEnvelope('/api/repository-approvals', payload || {});
}

// Task providers — where tickets live + which kato polls
// (KATO_ISSUE_PLATFORM). Has an active selector.
export function fetchTaskProviders() {
  return requestEnvelope('/api/task-providers');
}

export function updateTaskProvider(payload) {
  return postEnvelope('/api/task-providers', payload || {});
}

// Credentials for `provider` that already exist on this machine (gh/glab
// login, git credential helper, env var) — so the operator can connect
// without minting and pasting an API token. Never returns the token
// itself, only which sources work.
export function fetchCredentialSources(provider) {
  return requestEnvelope(
    `/api/credential-sources?provider=${encodeURIComponent(provider || '')}`,
  );
}

// Git hosts — credentials kato uses to clone / push / open PRs.
// NO active selector (host inferred from repo remote URLs).
export function fetchGitProviders() {
  return requestEnvelope('/api/git-providers');
}

export function updateGitProvider(payload) {
  return postEnvelope('/api/git-providers', payload || {});
}

// Schema-driven "all settings" tabs (General, Claude agent, Sandbox,
// Security scanner, Email & Slack, OpenHands, Docker/infra, AWS).
// One GET returns the whole schema + resolved values; POST writes a
// {KEY: value} map (server-side whitelisted to the schema).
export function fetchAllSettings() {
  return requestEnvelope('/api/all-settings');
}

export function updateAllSettings(updates) {
  return postEnvelope('/api/all-settings', { updates: updates || {} });
}

export function fetchActionGuardAudit(limit = 200) {
  return requestEnvelope(`/api/action-guard/audit?limit=${limit}`);
}

export function fetchAwaitingPushApproval(taskId) {
  if (!taskId) {
    return Promise.resolve({ awaiting_push_approval: false });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/awaiting-push-approval`,
  );
}

export function approveTaskPush(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/approve-push`,
    { method: 'POST' },
  );
}

export function fetchTaskPublishState(taskId) {
  if (!taskId) {
    return Promise.resolve({
      has_workspace: false, has_changes_to_push: false,
    });
  }
  // Bounded: drives the git buttons' enabled state, so it must never hang
  // "loading…" forever — on timeout it rejects and the UI shows the error.
  // Local-only on the backend (no provider call), so this is fast; the PR
  // check is a SEPARATE fetch (fetchTaskPullRequestState).
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/publish-state`,
    { timeoutMs: 8000 },
  );
}

// PR-existence for the task's Pull-request button + "open PR" link. A
// SEPARATE fetch from publish-state so a slow provider (429 retry backoff)
// can never freeze the git buttons — worst case only the PR button/link is
// briefly stale. Bounded; the caller keeps the git buttons usable even if
// this rejects.
export function fetchTaskPullRequestState(taskId) {
  if (!taskId) {
    return Promise.resolve({
      has_pull_request: false, pull_request_urls: [],
    });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/pull-request-state`,
    { timeoutMs: 8000 },
  );
}

// Content (grep) search across the task's workspace repos. Returns
// { matches: [{repo_id, path, line, text}], truncated, query }.
export function searchTaskWorkspaceContent(taskId, query) {
  const q = String(query || '').trim();
  if (!taskId || !q) {
    return Promise.resolve({ matches: [], truncated: false, query: q });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/search?q=${encodeURIComponent(q)}`,
  );
}

export function pushTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/push`,
    { method: 'POST' },
  );
}

export function pullTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/pull`,
    { method: 'POST' },
  );
}

// Fetch + merge each clone's default branch into the task branch.
// A conflicted merge is a 200 with ``has_conflicts: true`` — the
// caller surfaces it + tells the chat agent to resolve the markers.
export function mergeDefaultBranch(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/merge-default-branch`,
    { method: 'POST' },
  );
}

export function updateTaskSource(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/update-source`,
    { method: 'POST' },
  );
}

// Diff-tab review comments: list / create / resolve / reopen /
// delete + sync from the source git platform.
export function fetchTaskComments(taskId, repoId = '') {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  const params = repoId ? `?repo=${encodeURIComponent(repoId)}` : '';
  return requestEnvelopeStrict(
    `/api/sessions/${encodeURIComponent(taskId)}/comments${params}`,
  );
}

export function createTaskComment(taskId, comment) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments`,
    comment || {},
  );
}

export function resolveTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/resolve`,
    { method: 'POST' },
  );
}

export function markTaskCommentAddressed(taskId, commentId, addressedSha = '') {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/addressed`,
    { addressed_sha: addressedSha },
  );
}

export function reopenTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/reopen`,
    { method: 'POST' },
  );
}

export function retryTaskComment(taskId, commentId) {
  // Re-run a FAILED comment-run: the backend re-queues it and dispatches
  // immediately when the agent is idle.
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/retry`,
    { method: 'POST' },
  );
}

export function deleteTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}`,
    { method: 'DELETE' },
  );
}

// Edit a queued local comment. ``body`` and ``katoStatus`` are both
// optional — the inline edit UI sends ``{katoStatus: 'editing'}`` when
// the operator opens the editor (so the agent skips the comment), then
// ``{body, katoStatus: 'queued'}`` on save or ``{katoStatus: 'queued'}``
// on cancel.
export function editTaskComment(taskId, commentId, { body, katoStatus } = {}) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  const payload = {};
  if (typeof body === 'string') { payload.body = body; }
  if (katoStatus) { payload.kato_status = katoStatus; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/edit`,
    payload,
  );
}

// Every task assigned to the configured kato user — open, in
// progress, in review, done. Drives the left-panel "+ Add task"
// picker.
//
// We bound the wait with an AbortController. The endpoint
// synchronously calls into YouTrack / Jira; if the ticket platform
// is slow, rate-limited, or down, the modal would otherwise sit
// on "Loading tasks…" indefinitely. After the timeout we surface
// a short, operator-actionable error instead.
export async function fetchAllAssignedTasks({ timeoutMs = 30_000 } = {}) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeoutHandle = controller && typeof window !== 'undefined'
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  try {
    const response = await fetch(
      '/api/tasks',
      controller ? { signal: controller.signal } : undefined,
    );
    return strictEnvelopeFrom(response);
  } catch (err) {
    if (err && err.name === 'AbortError') {
      return {
        ok: false,
        error: `ticket platform did not respond within ${Math.round(timeoutMs / 1000)}s `
             + '— check kato logs and the YouTrack/Jira connection',
      };
    }
    return { ok: false, error: String(err) };
  } finally {
    if (timeoutHandle !== null) { window.clearTimeout(timeoutHandle); }
  }
}

// Adopt an existing assigned task — provision the workspace + clone
// every repo the task touches. No agent spawn; operator drives that
// from the chat tab once the workspace lands.
export function adoptTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/tasks/${encodeURIComponent(taskId)}/adopt`,
    { method: 'POST' },
  );
}

// Recent commits on a repo's task branch (newest first). Drives the
// Files-tab per-repo "view commit" dropdown. ``limit`` is optional
// (server caps it at 200); ``repoId`` is required.
export function fetchRepoCommits(taskId, repoId, { limit = 50 } = {}) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repoId) { return { ok: false, error: 'no repo id' }; }
  const params = new URLSearchParams({ repo: repoId, limit: String(limit) });
  return requestEnvelopeStrict(
    `/api/sessions/${encodeURIComponent(taskId)}/commits?${params}`,
  );
}

// Unified diff for a single commit on a repo. ``react-diff-view``'s
// parser eats the same shape ``/diff`` returns.
export function fetchRepoCommitDiff(taskId, repoId, sha) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repoId) { return { ok: false, error: 'no repo id' }; }
  if (!sha) { return { ok: false, error: 'no sha' }; }
  const params = new URLSearchParams({ repo: repoId, sha });
  return requestEnvelopeStrict(
    `/api/sessions/${encodeURIComponent(taskId)}/commit?${params}`,
  );
}


// Add any task repositories missing from the workspace. Pure additive
// — repos already cloned, and repos no longer on the task, stay on
// disk untouched. The Files-tab sync icon calls this when the
// operator's added a ``kato:repo:<name>`` tag in YouTrack and wants
// kato to fetch the new repo without re-running the whole task.
export function syncTaskRepositories(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/sync-repositories`,
    { method: 'POST' },
  );
}

// List every repository in kato's inventory (the chooser source for
// "+ Add repository"). The picker filters out repos already on the
// task UI-side so the same payload can power other chooser UIs.
export async function fetchInventoryRepositories() {
  const result = await requestEnvelopeStrict('/api/repositories');
  // This endpoint's error shape omits ``status`` — strip it back off.
  if (!result.ok) { return { ok: false, error: result.error }; }
  return result;
}

// Tag the task with ``kato:repo:<id>`` and clone the repo into the
// workspace. Atomic from the operator's perspective: one click,
// one toast, both halves done.
export function addTaskRepository(taskId, repositoryId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repositoryId) { return { ok: false, error: 'no repository id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/add-repository`,
    { repository_id: repositoryId },
  );
}

export function finishTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/finish`,
    { method: 'POST' },
  );
}

export function createTaskPullRequest(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/pull-request`,
    { method: 'POST' },
  );
}

export function fetchModels(force = false) {
  // ``force`` bypasses the server-side discovery cache so a refresh updates the
  // model labels (e.g. after a CLI upgrade) without a kato restart.
  return fetchJson(force ? '/api/models?refresh=1' : '/api/models');
}

// Live OpenRouter catalogue for the settings model-field autocomplete. Parallels
// fetchModels() (same ``{models}`` shape, ``/api/<x>/models`` route). Best-effort:
// a failed fetch yields an empty list so the field stays usable as free text.
export function fetchOpenRouterModels() {
  return fetchJson('/api/openrouter/models')
    .then((data) => (data && Array.isArray(data.models) ? data.models : []))
    .catch(() => []);
}

// Composer draft (in-progress prompt: text + pasted images) persisted
// server-side at <workspace>/.kato-prompts.json — survives refresh, a different
// browser, and task switches. Best-effort: a failed read yields an empty draft.
export function fetchDraft(taskId) {
  if (!taskId) { return Promise.resolve({ text: '', images: [] }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/draft`)
    .catch(() => ({ text: '', images: [] }));
}

export function saveDraft(taskId, draft) {
  if (!taskId) { return Promise.resolve({ ok: false }); }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/draft`,
    draft,
  );
}

export function fetchSessionModel(taskId) {
  if (!taskId) { return Promise.resolve({ model: '' }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/model`);
}

export function setSessionModel(taskId, modelId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/model`,
    { model: modelId },
  );
}

export function fetchEffortLevels() {
  // { levels: [...], default: '' } — levels discovered from the agent CLI.
  return fetchJson('/api/effort-levels');
}

export function fetchSessionEffort(taskId) {
  if (!taskId) { return Promise.resolve({ effort: '' }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/effort`);
}

export function setSessionEffort(taskId, effort) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/effort`,
    { effort: effort || '' },
  );
}

// Plan-mode lock: when on, the chat session spawns with Claude's
// ``--permission-mode plan`` so the agent only plans and never edits.
// Persisted per task server-side (survives reload / browser switch) and,
// like model/effort, applied on the next session (re)spawn.
export function fetchSessionPlanMode(taskId) {
  if (!taskId) { return Promise.resolve({ plan_mode: false }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/plan-mode`);
}

export function setSessionPlanMode(taskId, on) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/plan-mode`,
    { plan_mode: !!on },
  );
}

// The agent's captured plan (``<workspace>/plan.md``), written whenever
// the agent presents a plan via ExitPlanMode while in plan mode. Returns
// ``{ exists, content, mtime }``; ``mtime`` lets the caller detect a NEW
// plan (auto-open the centre pane only on a strictly-newer plan, never on
// every poll). Always resolves — empty payload for no plan / no task.
export function fetchSessionPlan(taskId) {
  if (!taskId) { return Promise.resolve({ exists: false, content: '', mtime: 0 }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/plan`)
    .catch(() => ({ exists: false, content: '', mtime: 0 }));
}

export function triggerScan() {
  return requestEnvelope('/api/scan/trigger', { method: 'POST' });
}

export function forgetTaskWorkspace(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/workspace`,
    { method: 'DELETE' },
  );
}

export function fetchFileTree(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/files`);
}

// Re-test push access for a read-only repo (the tree's "try again"). The
// envelope body carries ``{repo_id, read_only}`` — read_only flips false once
// kato can push (permission granted), and the caller reloads the tree.
export function recheckRepositoryPush(taskId, repoId) {
  if (!taskId || !repoId) { return Promise.resolve({ ok: false, error: 'missing id' }); }
  return requestEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}`
    + `/repositories/${encodeURIComponent(repoId)}/recheck-push`,
    { method: 'POST' },
  );
}

/**
 * Load a single tracked file's contents from the task workspace.
 * Server-side guards: path-traversal, 1MB cap, binary detection.
 * Returns ``{ ok, body }`` where body has either ``content`` (text),
 * ``binary: true`` (NUL bytes seen), ``too_large: true``, or (when
 * ``knownMtime`` is passed AND still matches the file's current mtime
 * on disk) ``unchanged: true`` with no ``content`` — the caller already
 * has it cached under that same mtime. ``knownMtime`` is always
 * VERIFIED against the server's own stat() on every call, never
 * trusted as-is — a background branch sync, merge, or a direct edit
 * outside kato can change the file with no SSE event the client would
 * ever see, so only the server confirming the mtime still matches
 * makes it safe to reuse cached content.
 */
export function fetchFileContent(taskId, absolutePath, knownMtime = '') {
  let url = `/api/sessions/${encodeURIComponent(taskId)}/file`
    + `?path=${encodeURIComponent(absolutePath)}`;
  if (knownMtime) {
    url += `&known_mtime=${encodeURIComponent(knownMtime)}`;
  }
  return fetchJson(url);
}

export async function fetchBaseFileContent(
  taskId,
  { repoId = '', repoCwd = '', path = '' } = {},
) {
  const query = new URLSearchParams();
  query.set('path', path);
  if (repoId) { query.set('repo', repoId); }
  const url = `/api/sessions/${encodeURIComponent(taskId)}/base-file`;
  const response = await fetch(`${url}?${query.toString()}`, { cache: 'no-store' });
  if (response.ok) { return response.json(); }
  const body = await response.json().catch(() => ({}));
  if (response.status === 404 && repoCwd && path && path !== '/dev/null') {
    const absolutePath = path.startsWith('/')
      ? path
      : `${repoCwd.replace(/\/+$/, '')}/${path}`;
    return fetchFileContent(taskId, absolutePath);
  }
  throw new Error(body.error || `${response.status} ${response.statusText}`);
}

export function fetchDiff(taskId, { repoId = '' } = {}) {
  const url = `/api/sessions/${encodeURIComponent(taskId)}/diff`;
  const query = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : '';
  return fetchJson(`${url}${query}`);
}

export function fetchClaudeSessions(query = '') {
  const qs = query ? `?q=${encodeURIComponent(query)}` : '';
  return fetchJson(`/api/claude/sessions${qs}`);
}

export function adoptAgentSession(taskId, agentSessionId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!agentSessionId) {
    return { ok: false, error: 'no agent session id' };
  }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/adopt-agent-session`,
    { [AGENT_SESSION_ID]: agentSessionId },
  );
}

// The task's chats — the active conversation plus the detached ones the
// operator can navigate back to (chats menu in the session header).
export function fetchTaskChats(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/chats`);
}

// Empty ``agentSessionId`` starts a FRESH chat (next message spawns a
// brand-new Claude session); a non-empty id switches back to one of the
// task's previous chats.
export function startTaskChat(taskId, agentSessionId = '') {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  return postEnvelope(
    `/api/sessions/${encodeURIComponent(taskId)}/chats`,
    { [AGENT_SESSION_ID]: agentSessionId },
  );
}

// Send a chat message with optional image attachments. The endpoint
// accepts the same shape as ``postSession(taskId, 'messages', {text})``
// but with an extra ``images`` array of ``{media_type, data}``
// entries. Kept separate from ``postSession`` so the call site reads
// "this is the message-with-attachments path" without having to
// know the body shape.
export async function postChatMessage(taskId, text, images = []) {
  if (!taskId) { return { ok: false, status: 0, error: 'no active task' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/messages`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text, images }),
      },
    );
    let resultBody = null;
    try { resultBody = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: (resultBody && resultBody.error) || response.statusText,
      };
    }
    return { ok: true, status: response.status, body: resultBody };
  } catch (err) {
    return { ok: false, status: 0, error: String(err) };
  }
}

export async function postSession(taskId, endpoint, body) {
  if (!taskId) {
    return { ok: false, status: 0, error: 'no active task' };
  }
  const init = { method: 'POST' };
  if (body !== undefined) {
    init.headers = { 'content-type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/${endpoint}`,
      init,
    );
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await safeReadError(response),
      };
    }
    let resultBody = null;
    try {
      resultBody = await response.json();
    } catch (_) { /* not all endpoints return json; that's fine */ }
    return { ok: true, status: response.status, body: resultBody };
  } catch (err) {
    return { ok: false, status: 0, error: String(err) };
  }
}

async function safeReadError(response) {
  try {
    const body = await response.json();
    return body.error || JSON.stringify(body);
  } catch (_) {
    return `${response.status} ${response.statusText}`;
  }
}
