// "Open the review loop view for task X" — from anywhere.
//
// The header chip, the header button and the transcript's loop messages all
// open the same centre-pane view, which App owns. A tiny pub/sub instead of a
// callback threaded through SessionDetail → SessionHeader / EventLog: App
// subscribes once and opens the view when a request names the task on screen.
//
// Also remembers which finished loops the operator has already looked at, so
// the header chip stops announcing an outcome once it has been seen.

import { createPubSub } from '../../stores/pubsub.js';

const SEEN_STORAGE_KEY = 'kato.reviewLoopSeen.v1';
const SEEN_LIMIT = 50;

let request = { taskId: '', seq: 0 };
const pubsub = createPubSub(() => request);

export const reviewLoopView = {
  // ``taskId`` '' means "the task on screen" — what the chat transcript, which
  // only ever shows that task, asks for.
  open(taskId = '') {
    request = { taskId: String(taskId || ''), seq: request.seq + 1 };
    pubsub.emit();
  },
  // Fires once on subscribe with the current request (seq 0 = none yet).
  subscribe: pubsub.subscribe,
};

// The open review-loop tabs (task ids) + which is active, remembered so a
// reload restores the set the operator had open — the same survive-a-reload
// treatment the file tabs get. App owns the live state; this is just its disk.
const TABS_STORAGE_KEY = 'kato.reviewLoopTabs.v1';

export function readReviewLoopTabs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(TABS_STORAGE_KEY) || '{}');
    const tabs = Array.isArray(parsed.tabs)
      ? [...new Set(parsed.tabs.map(String).filter(Boolean))]
      : [];
    const active = tabs.includes(String(parsed.active)) ? String(parsed.active) : (tabs[0] || '');
    return { tabs, active };
  } catch (_) {
    return { tabs: [], active: '' };
  }
}

export function writeReviewLoopTabs(tabs, active) {
  try {
    window.localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify({ tabs, active }));
  } catch (_) {
    // Storage full / blocked: the tabs still work this session, just won't
    // survive a reload.
  }
}

function readSeen() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SEEN_STORAGE_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch (_) {
    return [];
  }
}

export function isReviewLoopSeen(loopId) {
  return !!loopId && readSeen().includes(String(loopId));
}

export function markReviewLoopSeen(loopId) {
  if (!loopId || isReviewLoopSeen(loopId)) { return; }
  const next = [...readSeen(), String(loopId)].slice(-SEEN_LIMIT);
  try {
    window.localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(next));
  } catch (_) {
    // Storage full or blocked: the chip just keeps showing the outcome.
  }
}
