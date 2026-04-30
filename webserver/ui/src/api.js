// Thin fetch wrappers for the right-pane endpoints.
//
// The Flask app exposes:
//   GET /api/sessions/<task_id>/files  -> { cwd, tree }
//   GET /api/sessions/<task_id>/diff   -> { base, head, diff }
// Both return a JSON `error` field with non-2xx status when the session is
// missing or git refuses to cooperate; we surface that to the caller so the
// UI can render a clean message instead of an empty pane.

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

export function fetchFileTree(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/files`);
}

export function fetchDiff(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/diff`);
}
