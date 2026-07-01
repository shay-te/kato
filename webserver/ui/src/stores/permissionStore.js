// Single source of truth for pending permission (tool-approval) asks
// across EVERY task.
//
// Before this store the approval modal had TWO competing sources and the
// active task fell in the gap:
//   * the focused task's per-task SSE ``control_request`` frame (instant,
//     but a single small frame that a proxy can buffer / an idle-lifecycle
//     transition can clear — and nothing re-surfaces it), and
//   * a separate 3s poll that DELIBERATELY excluded the active task.
// So when the agent blocked on a permission, the operator saw the chat's
// "taking too long / continue" prompt instead of the dialog, and had to
// REFRESH the page (which replays the SSE backlog) to approve.
//
// Now there is ONE store, fed by two inputs and deduped by request id:
//   * ``refresh()`` polls ``/api/permissions/pending`` — the authoritative
//     server truth for every live session (this is what guarantees the
//     ask always surfaces, even when the SSE frame never arrived), and
//   * ``push()`` accepts the focused stream's live ``control_request`` so
//     the active task's dialog pops instantly instead of waiting a tick.
// One modal renders from this store for ALL tasks. A decision resolves
// the ask here (optimistically), and a short tombstone stops a poll that
// raced the decision from resurrecting it.
//
// Plain module-level pub/sub (mirrors ``toastStore`` / ``commentStore``):
// the ``usePendingPermissions`` hook is the React adapter, and the audit
// sink registry lets the focused chat still receive its "✓ approved"
// bubble even though the modal itself is owned globally.

import { fetchPendingPermissions } from '../api.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

const POLL_MS = 1500;
// Keep an un-polled SSE-pushed ask (and a just-resolved tombstone) alive
// this long, so a poll response that was in flight before the change
// can't briefly drop / resurrect it.
const GRACE_MS = 4000;

// requestId -> { envelope, addedAt, confirmed }
let _pending = new Map();
// requestId -> resolvedAt (tombstone so a racing poll won't re-add)
const _resolved = new Map();
let _error = '';
let _snapshot = { list: [], error: '' };

// taskId -> appendLocalEvent(bubble). Lets the globally-owned modal drop
// its approve/deny audit bubble into whichever task's chat is mounted.
const _auditSinks = new Map();

const _listeners = new Set();
let _timer = null;
let _inFlight = null;

function _now() { return Date.now(); }

function _rebuildSnapshot() {
  _snapshot = {
    list: Array.from(_pending.values(), (entry) => entry.envelope),
    error: _error,
  };
}

// Signature of the visible asks (request ids, in order) + error, so we
// only fan out when something a subscriber can see actually changed.
function _visibleSig() {
  const ids = Array.from(_pending.values(), (e) => (
    unpackPermissionEnvelope(e.envelope).requestId
  ));
  return `${ids.join('|')}#${_error}`;
}

let _lastSig = '#';
function _emitIfChanged() {
  const sig = _visibleSig();
  if (sig === _lastSig) { return; }
  _lastSig = sig;
  _rebuildSnapshot();
  for (const fn of _listeners) {
    try { fn(_snapshot); } catch (_) { /* isolate a throwing subscriber */ }
  }
}

function _pruneTombstones(now) {
  for (const [id, at] of _resolved) {
    if (now - at > GRACE_MS) { _resolved.delete(id); }
  }
}

function _reconcile(polled) {
  const now = _now();
  _pruneTombstones(now);
  const next = new Map();
  for (const envelope of polled) {
    const id = unpackPermissionEnvelope(envelope).requestId;
    if (!id || _resolved.has(id)) { continue; }
    const prev = _pending.get(id);
    next.set(id, {
      envelope,
      addedAt: prev ? prev.addedAt : now,
      confirmed: true,
    });
  }
  // Keep a young SSE-pushed ask the poll hasn't caught up to yet (its
  // originating server has the ask, so a later poll WILL include it — this
  // only bridges the sub-interval window and any in-flight poll).
  for (const [id, entry] of _pending) {
    if (next.has(id) || _resolved.has(id)) { continue; }
    if (!entry.confirmed && now - entry.addedAt < GRACE_MS) {
      next.set(id, entry);
    }
  }
  _pending = next;
  _emitIfChanged();
}

function _refresh() {
  if (_inFlight) { return _inFlight; }
  _inFlight = Promise.resolve()
    .then(() => fetchPendingPermissions())
    .then((body) => {
      const list = Array.isArray(body?.pending) ? body.pending : [];
      _error = '';
      _reconcile(list);
    })
    .catch(() => {
      // Keep the last-known asks on a transient failure — a blip must not
      // blank a dialog the operator is mid-decision on.
      _error = '';
    })
    .finally(() => { _inFlight = null; });
  return _inFlight;
}

function _startPolling() {
  if (_timer) { return; }
  const tick = () => {
    _timer = setTimeout(() => {
      if (typeof document === 'undefined' || !document.hidden) { _refresh(); }
      tick();
    }, POLL_MS);
  };
  tick();
}

function _stopPolling() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
}

export const permissionStore = {
  subscribe(fn) {
    _listeners.add(fn);
    try { fn(_snapshot); } catch (_) { /* see _emitIfChanged */ }
    if (_listeners.size === 1) {
      _startPolling();
      _refresh();
    }
    return () => {
      _listeners.delete(fn);
      if (_listeners.size === 0) { _stopPolling(); }
    };
  },

  getSnapshot() { return _snapshot; },

  // Instant surfacing for the focused task: accept its live
  // ``control_request`` before the poll catches up. Deduped by request id
  // and ignored if the ask was just resolved.
  push(taskId, raw, taskSummary = '') {
    const { requestId } = unpackPermissionEnvelope(raw);
    if (!requestId || _resolved.has(requestId) || _pending.has(requestId)) { return; }
    const envelope = { ...raw, task_id: taskId, task_summary: taskSummary };
    _pending.set(requestId, { envelope, addedAt: _now(), confirmed: false });
    _emitIfChanged();
  },

  // Remove an ask the operator (or a remembered decision) just answered,
  // and tombstone it so a poll that raced the decision won't re-add it.
  resolve(requestId) {
    const id = String(requestId || '');
    if (!id) { return; }
    _resolved.set(id, _now());
    if (_pending.delete(id)) { _emitIfChanged(); }
  },

  // Force an immediate reconcile (e.g. right after a decision posts).
  refresh() { return _refresh(); },

  // Whether a given task currently has a pending ask — drives the focused
  // chat's "waiting for approval" indicator off the reliable source.
  hasPendingForTask(taskId) {
    const target = String(taskId || '');
    if (!target) { return false; }
    for (const entry of _pending.values()) {
      if (String(unpackPermissionEnvelope(entry.envelope).taskId) === target) {
        return true;
      }
    }
    return false;
  },

  registerAuditSink(taskId, fn) {
    const target = String(taskId || '');
    if (!target || typeof fn !== 'function') { return () => {}; }
    _auditSinks.set(target, fn);
    return () => {
      if (_auditSinks.get(target) === fn) { _auditSinks.delete(target); }
    };
  },

  emitAudit(taskId, bubble) {
    const fn = _auditSinks.get(String(taskId || ''));
    if (fn) { try { fn(bubble); } catch (_) { /* isolate */ } }
  },

  // Test-only: clear all state so this module singleton doesn't leak
  // pending asks / tombstones between tests.
  __resetForTests() {
    _stopPolling();
    _pending = new Map();
    _resolved.clear();
    _auditSinks.clear();
    _error = '';
    _lastSig = '#';
    _inFlight = null;
    _rebuildSnapshot();
  },
};
