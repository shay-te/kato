async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.error) { message = body.error; }
    } catch (_) { /* fall through with status text */ }
    throw new Error(message);
  }
  return response.json();
}

export function fetchSessionList() {
  return fetchJson('/api/sessions');
}

export function fetchSafetyState() {
  return fetchJson('/api/safety');
}

export function fetchAwaitingPushApproval(taskId) {
  if (!taskId) {
    return Promise.resolve({ awaiting_push_approval: false });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/awaiting-push-approval`,
  );
}

export async function approveTaskPush(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/approve-push`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchTaskPublishState(taskId) {
  if (!taskId) {
    return Promise.resolve({
      has_workspace: false, has_pull_request: false,
    });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/publish-state`,
  );
}

export async function pushTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/push`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateTaskSource(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/update-source`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function finishTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/finish`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function createTaskPullRequest(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/pull-request`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function forgetTaskWorkspace(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/workspace`,
      { method: 'DELETE' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchFileTree(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/files`);
}

export function fetchDiff(taskId, { repoId = '' } = {}) {
  const url = `/api/sessions/${encodeURIComponent(taskId)}/diff`;
  const query = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : '';
  return fetchJson(`${url}${query}`);
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
