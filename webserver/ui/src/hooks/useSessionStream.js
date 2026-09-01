import { useEffect, useReducer, useRef, useState } from 'react';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import {
  CODEX_EVENT, CODEX_ITEM, isCodexTerminal,
} from '../constants/codexEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { safeParseJSON } from '../utils/sse.js';

export const SESSION_LIFECYCLE = {
  CONNECTING: 'connecting',
  IDLE: 'idle',           // record exists but no live subprocess
  STREAMING: 'streaming', // events flowing
  CLOSED: 'closed',
  MISSING: 'missing',     // server has no record for this task
};

// How long to wait before re-opening the stream for a tab the server told us
// is IDLE. Idle is not terminal — kato can spawn a subprocess for this task at
// any moment (the autonomous scan picking it up, a queued comment draining,
// workspace provisioning finishing) and the closed stream would never hear it.
// Backoff rather than a fixed interval because every reconnect makes the
// server replay the FULL JSONL transcript: quick while the operator is most
// likely watching a task they just started, then settling to a cheap poll.
const IDLE_RETRY_MIN_MS = 2000;
const IDLE_RETRY_MAX_MS = 30000;

const ACTION_HYDRATE = 'hydrate';
const ACTION_INCOMING_EVENT = 'incoming_event';
const ACTION_INCOMING_HISTORY = 'incoming_history';
const ACTION_LIFECYCLE = 'lifecycle';
const ACTION_LOCAL_EVENT = 'local_event';
const ACTION_DISMISS_PERMISSION = 'dismiss_permission';
const ACTION_MARK_TURN_BUSY = 'mark_turn_busy';

// Per-task chat state lives in this module-level Map so it survives the
// SessionDetail unmount/remount cycle that React triggers on tab switch
// (see App.jsx `<SessionDetail key={activeSessionKey} />`). Without this
// cache, switching tabs blows away every LOCAL bubble ("✓ delivered",
// "✗ session stopped", in-flight typed messages) plus any kato-injected
// synthetic event that lives only in the server's `recent_events`
// buffer — the operator sees the chat "shrink" by however many of
// those entries had accumulated. Hydrating from the cache restores the
// previously-seen entries; dedupe on incoming SSE replay (history +
// backlog) prevents the server from doubling them.
const TASK_STREAM_CACHE = new Map();

let _localEventCounter = 0;

function emptyTaskState() {
  return {
    events: [],
    eventKeys: new Set(),
    // Exactly what the operator typed, for each message they sent. Kept
    // apart from ``eventKeys`` because these are matched by SUFFIX, not by
    // equality — see ``matchesAnEcho``.
    echoTexts: new Set(),
    // How many times each text identity has been APPENDED. Makes the text
    // key positional so a genuinely repeated message survives — see
    // ``occurrenceKey``.
    sharedCounts: new Map(),
    lifecycle: SESSION_LIFECYCLE.CONNECTING,
    turnInFlight: false,
    // The just-closed turn scheduled a long background wait (Monitor /
    // run_in_background) and is blocked on it — still "working", not idle.
    // Cleared when the next turn starts or the session closes.
    awaitingBackground: false,
    // The outstanding background wait is specifically a Workflow (the
    // ultracode orchestrator), so the status surfaces read "workflow" in
    // its own colour rather than the generic "working".
    backgroundIsWorkflow: false,
    // Transient: a background-wait tool_use was seen in the OPEN turn, so
    // the upcoming RESULT knows to flip ``awaitingBackground``.
    turnHasBackgroundWait: false,
    // Transient sibling: that background wait was a Workflow.
    turnHasWorkflow: false,
    pendingPermission: null,
    lastEventAt: 0,
  };
}

// Tools that park the agent on a long-running wait (it scheduled work and
// is blocked on its result). MUST match the backend
// StreamingClaudeSession._BACKGROUND_WAIT_TOOLS ({'Monitor', 'Workflow'}).
const BACKGROUND_WAIT_TOOLS = new Set(['Monitor', 'Workflow']);
// The subset that is a background WORKFLOW — surfaced as its own status.
const WORKFLOW_TOOLS = new Set(['Workflow']);

function eventBackgroundWaitTools(raw) {
  const content = raw && raw.message && raw.message.content;
  if (!Array.isArray(content)) { return { wait: false, workflow: false }; }
  let wait = false;
  let workflow = false;
  for (const block of content) {
    if (!block || block.type !== 'tool_use') { continue; }
    if (WORKFLOW_TOOLS.has(block.name)) { wait = true; workflow = true; }
    else if (BACKGROUND_WAIT_TOOLS.has(block.name)) { wait = true; }
    else if (block.input && block.input.run_in_background === true) { wait = true; }
  }
  return { wait, workflow };
}

function eventHasBackgroundWaitTool(raw) {
  return eventBackgroundWaitTools(raw).wait;
}

function readCachedState(taskId) {
  if (!taskId) { return emptyTaskState(); }
  let entry = TASK_STREAM_CACHE.get(taskId);
  if (!entry) {
    entry = emptyTaskState();
    TASK_STREAM_CACHE.set(taskId, entry);
  }
  return entry;
}

function writeCachedState(taskId, state) {
  if (!taskId) { return; }
  TASK_STREAM_CACHE.set(taskId, state);
}

function entryDedupeKey(entry) {
  // LOCAL entries get a synthetic monotonic id at creation; we can
  // never confuse a local bubble with a server replay, so the id alone
  // is enough.
  if (entry.source === ENTRY_SOURCE.LOCAL) {
    return `local:${entry.localId}`;
  }
  // SERVER entries: prefer the per-event ``received_at_epoch`` the
  // server stamps on each ``SessionEvent``. It's a high-resolution
  // timestamp captured when kato received the event from Claude's
  // stdout, and it's preserved across replays — so a backlog re-emit
  // of the same event reuses the same key. JSON.stringify(raw) is a
  // BAD fallback here: two distinct live events with identical
  // payload (e.g., a respawned Claude emitting another
  // ``system { subtype: init }`` for the same session id) would
  // collide and the second would be silently dropped, freezing the UI
  // until something with different content arrives. The epoch is
  // unique-per-event by construction, so it can't collide.
  if (entry.source === ENTRY_SOURCE.SERVER) {
    const epoch = Number(entry.receivedAtEpoch || 0);
    if (epoch > 0) {
      return `server:${epoch}`;
    }
    return `server:${rawFingerprint(entry.raw)}`;
  }
  // HISTORY entries key on a compact content fingerprint — NOT the epoch.
  // (The epoch is now the JSONL timestamp, carried for display only; keeping
  // identity content-based means replays of the same JSONL dedupe stably and
  // we avoid the JSON.stringify cost on long histories.)
  return `history:${rawFingerprint(entry.raw)}`;
}

// Compact identity for a Claude raw event. Most events the SDK
// emits carry a ``uuid``; assistant/user envelopes carry an
// Anthropic ``message.id``; tool results carry a ``tool_use_id``.
// Any of those uniquely identify the event without walking the
// (potentially huge) prompt / tool-output payload. Falling back to
// a type+subtype+session triple is good enough for the rare event
// shape that lacks all three — collisions there only over-dedupe,
// they don't drop distinct content.
function rawFingerprint(raw) {
  if (!raw || typeof raw !== 'object') { return 'none'; }
  if (raw.uuid) { return `u:${raw.uuid}`; }
  const messageId = raw.message && raw.message.id;
  if (messageId) { return `m:${messageId}`; }
  if (raw.tool_use_id) { return `t:${raw.tool_use_id}`; }
  return `s:${raw.type || ''}:${raw.subtype || ''}:${raw[AGENT_SESSION_ID] || ''}`;
}

// A CROSS-SOURCE identity, or '' when the event carries none.
//
// The per-source keys above deliberately differ: SERVER keys on the receive
// epoch (two distinct live events can share a payload), HISTORY on content.
// That meant one logical message arriving BOTH ways — live over SSE and again
// in the JSONL replay when a chat is resumed or reopened — produced two keys
// and rendered twice. On a long plan that is two full copies of the answer,
// which is what the operator sees as "he prints the output twice".
//
// Only STRONG identities qualify. The weak ``type:subtype:session`` fallback
// must not dedupe across sources: two genuinely distinct events (a respawned
// CLI emitting another ``system init``) share it, and dropping the second
// would freeze the transcript.
function crossSourceIdentity(raw) {
  if (!raw || typeof raw !== 'object') { return ''; }
  // Operator prompts are matched on TEXT, and deliberately BEFORE the uuid.
  //
  // A uuid looks like the stronger identity, and for everything else it is —
  // but Claude assigns one per JSONL RECORD, not per logical message. The
  // live SSE event and the transcript replay of the same prompt therefore
  // carry DIFFERENT uuids, so keying on it gave one prompt two identities and
  // it rendered twice. Assistant turns escaped this because they also carry
  // an Anthropic ``message.id``, which IS stable across both sources; a user
  // prompt has no such id, which is why only the prompt duplicated.
  //
  // Same trade-off already accepted for Codex below: two genuinely identical
  // consecutive prompts ("continue", "continue") collapse into one. That is
  // the cost of having no stable id, and it is far cheaper than a transcript
  // that reprints the operator's own question after every reconnect.
  //
  // ``!raw.tool_use_id`` matters: a tool RESULT is also delivered as a
  // ``user`` envelope, and it carries no operator text — so without this
  // guard it got an empty identity here and lost the cross-source dedupe it
  // already had on ``tool_use_id``, printing every tool result twice.
  if (raw.type === CLAUDE_EVENT.USER && !raw.tool_use_id) {
    const text = userMessageTextFor(raw);
    return text ? `x:cu:${text}` : '';
  }
  // NOTE: this key is deliberately NOT positional. It is made positional by
  // ``occurrenceKey`` at the call site, which is where the running count
  // lives — see there for why a global text key deletes real messages.
  if (raw.uuid) { return `x:u:${raw.uuid}`; }
  const messageId = raw.message && raw.message.id;
  if (messageId) { return `x:m:${messageId}`; }
  if (raw.tool_use_id) { return `x:t:${raw.tool_use_id}`; }
  return codexCrossSourceIdentity(raw);
}

// Codex's turns carry NO id — not a uuid, not a message id. That matters
// because a live chat replays from two sources at once: the CLI's rollout
// transcript on disk (everything before this process started) and the live
// session's in-memory log (everything since). With no identity to match on,
// every prompt and reply present in both rendered TWICE.
//
// The content is the identity here. That is weaker than an id and the cost
// is real — two genuinely identical consecutive turns ("continue",
// "continue") collapse into one — but it is bounded to the two shapes that
// have no id at all, and the alternative is a transcript that doubles every
// message the moment the operator sends one after a restart.
function codexCrossSourceIdentity(raw) {
  // NOTE: ``user`` is handled by ``crossSourceIdentity`` above, for BOTH
  // backends — it has to run before the uuid check, and duplicating the
  // branch here would leave two copies of the same rule to drift apart.
  if (raw.type === CODEX_EVENT.ITEM_COMPLETED) {
    const item = raw.item || {};
    if (item.type !== CODEX_ITEM.AGENT_MESSAGE) { return ''; }
    const text = String(item.text || '').trim();
    return text ? `x:ca:${text}` : '';
  }
  return '';
}

// The text of a ``user`` envelope, for identity only.
function userMessageTextFor(raw) {
  const content = (raw.message && raw.message.content) || [];
  // A prompt arrives as a plain string on some paths and as content blocks on
  // others. Returning '' for the string shape would leave exactly those
  // prompts with no identity — and therefore still duplicating.
  if (typeof content === 'string') { return content.trim(); }
  if (!Array.isArray(content)) { return ''; }
  return content
    .filter((block) => block && block.type === 'text' && block.text)
    .map((block) => String(block.text))
    .join('\n')
    .trim();
}

// The cross-source identity of an entry, INCLUDING the operator's own local
// echo bubble.
//
// The echo is the message the operator just typed, rendered immediately so
// the composer feels responsive. The server later replays that same message
// as a ``user`` event — and on a reconnect it arrives inside a full history
// replay, hundreds of entries after the cached echo.
//
// A bounded display filter (``MessageFilter.dedupeUserEchoes``, 4 entries)
// only ever caught the adjacent case. Giving the echo the SAME text identity
// every other copy gets makes the distance irrelevant, which is the actual
// shape of the bug: the two copies sat far apart with a whole agent turn
// between them.
function sharedIdentityOf(entry) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) {
    if (entry.kind !== 'user') { return ''; }
    const text = String(entry.text || '').trim();
    return text ? `x:cu:${text}` : '';
  }
  return crossSourceIdentity(entry?.raw);
}

// The text the operator typed, if this entry is their own echo bubble.
function echoTextOf(entry) {
  if (entry?.source !== ENTRY_SOURCE.LOCAL) { return ''; }
  if (entry.kind !== 'user') { return ''; }
  return String(entry.text || '').trim();
}

// Is ``text`` a replay of something the operator typed?
//
// Equality is not enough, and assuming it was is why the first attempt at
// this did nothing. What the operator types is NOT what the agent receives:
// on a spawn the server prepends a context preamble — the workspace scope /
// STRICT BOUNDARY block, the continuity block, the forbidden-repository
// guardrails — joined to the message with a blank line
// (``'\n\n'.join([...blocks, prompt])`` in agent_prompt_utils). The
// transcript records that whole envelope, so the replayed \`user\` event is
// the preamble PLUS the message, and never equals the echo.
//
// The message is always the TAIL, so a suffix match is the right test. The
// boundary check keeps it honest: only a match that begins at a line break
// counts, so "yes" cannot be swallowed by an unrelated prompt ending in the
// word "yes".
function matchesAnEcho(text, echoTexts) {
  const body = String(text || '').trim();
  if (!body || !echoTexts || echoTexts.size === 0) { return false; }
  for (const echo of echoTexts) {
    if (!echo) { continue; }
    if (body === echo) { return true; }
    if (body.length > echo.length && body.endsWith(echo)) {
      const boundary = body[body.length - echo.length - 1];
      if (boundary === '\n') { return true; }
    }
  }
  return false;
}

// A text identity, made unique per OCCURRENCE.
//
// A bare text key says "this message has been seen", which silently deletes
// the operator's SECOND identical message. That is not hypothetical: kato
// itself sends "continue" and "Please continue from where you left off."
// verbatim, and the operator's own transcripts contain each of them seven
// times. On a reload both copies arrive as history — the local-echo bypass
// cannot save them — so the second turn's output ends up filed under the
// first turn's prompt header.
//
// Counting occurrences keeps the dedupe (a REPLAY of occurrence #2 still
// matches occurrence #2) while letting a genuinely repeated message through.
// Which of the three streams an entry arrived on. One logical message can
// appear on all three: the operator's own echo, the live SSE event, and the
// transcript replay.
function streamOf(entry) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) { return 'local'; }
  if (entry?.source === ENTRY_SOURCE.HISTORY) { return 'history'; }
  return 'server';
}

function occurrenceBucket(shared, entry) {
  return shared ? `${streamOf(entry)}|${shared}` : '';
}

function occurrenceKey(shared, entry, counts) {
  if (!shared) { return ''; }
  // Counted WITHIN the entry's own stream, then dropped from the key.
  //
  // Counting globally is wrong in the other direction: the replay increments
  // the same counter the live copy then reads, so the two halves of ONE
  // message land on different indices and both render — the very bug this
  // key exists to prevent.
  //
  // Per stream, the Nth "continue" from history matches the Nth "continue"
  // from the live stream (same message, deduped) while the operator's SECOND
  // "continue" is index 1 and survives (different message, kept).
  const bucket = occurrenceBucket(shared, entry);
  // Tolerates a state built before this field existed (a cached snapshot
  // from a previous build) — without it the append path throws and the
  // transcript stops updating.
  const seen = (counts && counts.get(bucket)) || 0;
  return `${shared}#${seen}`;
}

function appendEntryIfNew(state, entry) {
  const key = entryDedupeKey(entry);
  if (state.eventKeys.has(key)) {
    return { state, appended: false };
  }
  const shared = sharedIdentityOf(entry);
  // A LOCAL echo is never suppressed by this — it is the operator's own
  // action and has to appear the moment they send it. Sending the same words
  // twice ("continue", "continue") must render twice. It still REGISTERS the
  // identity, so the replayed copies of both are the ones that get dropped.
  const isLocal = entry?.source === ENTRY_SOURCE.LOCAL;
  const positional = occurrenceKey(shared, entry, state.sharedCounts);
  // Counted HERE, before the drop decision — every new record on this stream
  // advances its stream's counter, whether or not it survives.
  //
  // Counting only appended entries breaks the scheme: a live copy suppressed
  // by its history twin never advances, so the NEXT live copy is still
  // "occurrence 0" and matches the same twin. Three sends of "continue"
  // collapsed to one that way.
  const bucket = occurrenceBucket(shared, entry);
  if (bucket && state.sharedCounts) {
    state.sharedCounts.set(bucket, (state.sharedCounts.get(bucket) || 0) + 1);
  }
  if (!isLocal && positional && state.eventKeys.has(positional)) {
    return { state, appended: false };
  }
  // A replayed copy of something the operator typed, carrying the server's
  // prepended context preamble so it does not match by equality.
  if (!isLocal && entry?.raw?.type === CLAUDE_EVENT.USER
      && !entry.raw.tool_use_id
      && matchesAnEcho(userMessageTextFor(entry.raw), state.echoTexts)) {
    return { state, appended: false };
  }
  state.eventKeys.add(key);
  if (positional) { state.eventKeys.add(positional); }
  const echo = echoTextOf(entry);
  if (echo) { state.echoTexts.add(echo); }
  return {
    state: {
      ...state,
      events: [...state.events, entry],
      eventKeys: state.eventKeys,
    },
    appended: true,
  };
}

// Rebuild the dedupe Set from an ``events`` array. Used on HYDRATE: the
// module cache stores reducer state BY REFERENCE and ``appendEntryIfNew``
// mutates ``eventKeys`` in place, so a cached snapshot's Set can run AHEAD of
// the (older) ``events`` array it was stored with — when a newer append's
// state object never reached the cache-write effect before the tab unmounted.
// Those phantom keys would make the server's history re-replay dedupe AWAY the
// very entries the snapshot is missing, so the transcript renders short/blank
// on switch-back. Recomputing the Set from the actual events makes the cache
// self-correcting: missing turns get re-appended by the replay, present ones
// stay deduped. O(N) once per remount — cheaper than cloning the Set per event.
function echoTextsFromEvents(events) {
  const texts = new Set();
  for (const entry of events || []) {
    const echo = echoTextOf(entry);
    if (echo) { texts.add(echo); }
  }
  return texts;
}

function sharedCountsFromEvents(events) {
  const counts = new Map();
  for (const entry of events || []) {
    const bucket = occurrenceBucket(sharedIdentityOf(entry), entry);
    if (bucket) { counts.set(bucket, (counts.get(bucket) || 0) + 1); }
  }
  return counts;
}

function keysFromEvents(events) {
  const keys = new Set();
  const counts = new Map();
  for (const entry of events || []) {
    keys.add(entryDedupeKey(entry));
    // ``sharedIdentityOf``, not ``crossSourceIdentity``: this rebuild runs on
    // HYDRATE, which is precisely the reconnect path the duplicate appears
    // on. The cached transcript still holds the operator's local echo, and
    // the server is about to replay that same prompt — so if the echo's
    // identity is not restored here, the replayed copy has nothing to match
    // against and renders a second time.
    const shared = sharedIdentityOf(entry);
    if (shared) {
      // Same per-stream scheme as the append path, or a rebuilt set would
      // not match the keys the appends produced.
      const bucket = occurrenceBucket(shared, entry);
      const seen = counts.get(bucket) || 0;
      keys.add(`${shared}#${seen}`);
      counts.set(bucket, seen + 1);
    }
  }
  return keys;
}

// Exported for unit tests. Pure function — the hook is just a thin
// `useReducer` wrapper around this. Tests pass in `{type, ...}` actions
// with the constants below as type strings ("lifecycle", "mark_turn_busy",
// "incoming_event", "incoming_history", "hydrate", "dismiss_permission").
export function reducer(state, action) {
  switch (action.type) {
    case ACTION_HYDRATE:
      // Rebuild eventKeys from the hydrated events so a cached snapshot whose
      // shared Set drifted ahead of its events array can't suppress the SSE
      // history re-replay — otherwise the transcript shrinks/blanks on switch.
      return {
        ...action.value,
        eventKeys: keysFromEvents(action.value.events),
        sharedCounts: sharedCountsFromEvents(action.value.events),
        // Rebuilt for the same reason as ``eventKeys``: hydrate restores a
        // cached transcript and the server then replays its history over
        // it. Without the operator's echoes here, every one of their
        // messages comes back a second time.
        echoTexts: echoTextsFromEvents(action.value.events),
      };
    case ACTION_INCOMING_EVENT: {
      const next = reduceIncomingEvent(state, action.event, action.receivedAtEpoch);
      // Live events also imply lifecycle=STREAMING. Folding the
      // transition into the same reducer pass means one re-render
      // per event instead of two — used to be a separate
      // ACTION_LIFECYCLE dispatch from the SSE handler.
      if (next.lifecycle !== SESSION_LIFECYCLE.STREAMING) {
        return { ...next, lifecycle: SESSION_LIFECYCLE.STREAMING };
      }
      return next;
    }
    case ACTION_INCOMING_HISTORY:
      return reduceIncomingHistory(state, action.event, action.receivedAtEpoch);
    case ACTION_LOCAL_EVENT: {
      _localEventCounter += 1;
      const enriched = { ...action.event, localId: _localEventCounter };
      return appendEntryIfNew(state, enriched).state;
    }
    case ACTION_LIFECYCLE:
      // CLOSED / IDLE / MISSING all mean "nothing live is waiting for input"
      // — drop any stale permission AND reset turnInFlight. Without
      // the turnInFlight reset, the WorkingIndicator stays "Claude is
      // thinking…" forever on a subprocess that died mid-turn (no
      // RESULT event was emitted before the subprocess exited).
      if (action.value === SESSION_LIFECYCLE.CLOSED
          || action.value === SESSION_LIFECYCLE.IDLE
          || action.value === SESSION_LIFECYCLE.MISSING) {
        return {
          ...state,
          lifecycle: action.value,
          pendingPermission: null,
          turnInFlight: false,
          awaitingBackground: false,
          backgroundIsWorkflow: false,
          turnHasBackgroundWait: false,
          turnHasWorkflow: false,
        };
      }
      return { ...state, lifecycle: action.value };
    case ACTION_DISMISS_PERMISSION:
      return { ...state, pendingPermission: null };
    case ACTION_MARK_TURN_BUSY:
      // A freshly-sent message is a new turn — it supersedes any prior
      // background wait.
      return {
        ...state,
        turnInFlight: action.value,
        awaitingBackground: action.value ? false : state.awaitingBackground,
        backgroundIsWorkflow: action.value ? false : state.backgroundIsWorkflow,
      };
    default:
      return state;
  }
}

// Fold the three permission/result transitions that both the live
// reducer (``reduceIncomingEvent``) and the history reducer
// (``reduceIncomingHistory``) share:
//
//   * PERMISSION_REQUEST / CONTROL_REQUEST → arm ``pendingPermission``
//   * RESULT                               → clear ``pendingPermission``
//   * PERMISSION_RESPONSE                  → conditionally clear it
//
// ``next`` is the in-progress next state (mutated in place — both
// callers already cloned it). ``raw`` is the wire event. ``prevState``
// is the PRE-mutation state, read for the pending-permission id we are
// matching against. ``strict`` selects the response-matching policy:
//
//   strict=true  (LIVE): clear ONLY on a positive match — both ids
//     present and equal, OR the pending side has no id at all (legacy
//     shape we can't verify). A mismatched / empty-id response must
//     NOT wipe a legitimate pending modal. Reads only ``raw.request_id``.
//
//   strict=false (HISTORY): lenient self-heal — clear on empty
//     respondedId OR empty pendingId OR an exact match. A replayed
//     history with a missing response id should still settle a stale
//     pending. Reads ``raw.request_id`` OR the nested
//     ``raw.request.request_id`` envelope.
//
// This carries the documented lazy-resume / wake / pending-permission
// self-heal behavior — the strict-vs-lenient split is load-bearing.
// Returns true when the event type was one of the three handled cases
// (callers don't otherwise need the flag, but it keeps the contract
// explicit).
function applyPermissionTransition(next, raw, prevState, { strict }) {
  switch (raw?.type) {
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
      next.pendingPermission = raw;
      return true;
    case CLAUDE_EVENT.RESULT:
      next.pendingPermission = null;
      return true;
    case CLAUDE_EVENT.PERMISSION_RESPONSE: {
      const pendingId = pendingRequestId(prevState.pendingPermission);
      if (strict) {
        const respondedId = String(raw.request_id || '');
        // Only clear pending when we can MATCH the response to it.
        // Previously we also cleared on empty respondedId — but an
        // unrelated response (e.g., synthetic event with no id) would
        // then wipe a legitimate pending modal. Require a positive
        // match: either both ids present and equal, OR the pending
        // side has no id at all (legacy shape with no way to verify).
        if (pendingId && respondedId && respondedId === pendingId) {
          next.pendingPermission = null;
        } else if (!pendingId && prevState.pendingPermission) {
          // Pending exists but has no id — best-effort clear so we
          // don't deadlock on a malformed legacy event.
          next.pendingPermission = null;
        }
      } else {
        const respondedId = String(raw.request_id || raw.request?.request_id || '');
        if (!respondedId || !pendingId || respondedId === pendingId) {
          next.pendingPermission = null;
        }
      }
      return true;
    }
    default:
      return false;
  }
}

function reduceIncomingEvent(state, raw, receivedAtEpoch) {
  const entry = {
    source: ENTRY_SOURCE.SERVER,
    raw,
    receivedAtEpoch: Number(receivedAtEpoch || 0),
  };
  const { state: appended } = appendEntryIfNew(state, entry);
  // Always advance the activity clock + lifecycle hooks, even when
  // dedupe drops the entry (e.g., backlog replay re-emits an event
  // we already cached). The bubble doesn't get rendered twice but
  // activity tracking still sees the heartbeat — without this, the
  // WorkingIndicator trips its "stalled" threshold during a healthy
  // live stream and only un-trips on tab switch (when remount
  // forces a hydrate that includes a freshly-stamped lastEventAt).
  const next = appended === state ? { ...state } : appended;
  next.lastEventAt = Date.now();
  // Codex speaks a different wire vocabulary from Claude — a thread/turn
  // lifecycle instead of assistant/result. Handled BEFORE the Claude switch
  // because none of its cases match, so a Codex turn set "working" at spawn
  // and never cleared it: the indicator span forever and the status chip sat
  // on "working" until a tab switch remounted and re-derived it.
  if (isCodexTerminal(raw?.type)) {
    next.turnInFlight = false;
    next.awaitingBackground = false;
    next.backgroundIsWorkflow = false;
    return next;
  }
  if (raw?.type === CODEX_EVENT.TURN_STARTED
      || raw?.type === CODEX_EVENT.ITEM_COMPLETED) {
    next.turnInFlight = true;
    next.awaitingBackground = false;
    next.backgroundIsWorkflow = false;
    return next;
  }

  // turnInFlight is a LIVE-only concern; keep it inline here. The
  // shared permission/result clearing is folded into
  // applyPermissionTransition (strict, positive-match policy).
  switch (raw?.type) {
    case CLAUDE_EVENT.SYSTEM:
      // A fresh ``system/init`` is the EARLIEST wire signal that a turn
      // has begun: autonomous task prompts are written to Claude's
      // stdin (never echoed back as a ``user`` event) and partial
      // ``stream_event`` deltas are disabled, so the only thing that
      // precedes the first ``assistant`` event is ``init``. Flipping
      // "working" here means the status pill stops lagging behind the
      // "Claude session started" bubble — previously it sat on "idle"
      // for the multi-second window while Claude read context before
      // its first reply. A crash before ``result`` is still cleared by
      // the CLOSED/IDLE/MISSING lifecycle reset above; an idle session
      // reconnect replays its trailing ``result`` (backlog flows
      // through this same live path) and settles back to idle. Only
      // INIT counts — PREFLIGHT (workspace cloning) is masked by the
      // PROVISIONING status anyway.
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
        next.turnInFlight = true;
        next.awaitingBackground = false;
        next.backgroundIsWorkflow = false;
      }
      break;
    case CLAUDE_EVENT.ASSISTANT: {
      next.turnInFlight = true;
      next.awaitingBackground = false;
      next.backgroundIsWorkflow = false;
      // Remember a background-wait tool seen this turn so the closing
      // RESULT can keep the status "working" while the agent waits on it —
      // and whether that wait was a Workflow (its own status).
      const bg = eventBackgroundWaitTools(raw);
      if (bg.wait) { next.turnHasBackgroundWait = true; }
      if (bg.workflow) { next.turnHasWorkflow = true; }
      break;
    }
    case CLAUDE_EVENT.RESULT:
      // RESULT ends the turn AND clears pending. If the turn scheduled a
      // background wait (Monitor / Workflow / run_in_background), stay
      // "working" (awaitingBackground) until the next turn or session close.
      next.turnInFlight = false;
      next.awaitingBackground = !!state.turnHasBackgroundWait;
      next.backgroundIsWorkflow = !!state.turnHasWorkflow;
      next.turnHasBackgroundWait = false;
      next.turnHasWorkflow = false;
      applyPermissionTransition(next, raw, state, { strict: true });
      break;
    default:
      applyPermissionTransition(next, raw, state, { strict: true });
      break;
  }
  return next;
}

function reduceIncomingHistory(state, raw, receivedAtEpoch) {
  // ``receivedAtEpoch`` (from the JSONL timestamp) is display-only here — the
  // history dedupe key is a content fingerprint, not the epoch — so a replayed
  // prompt can still show WHEN it was asked.
  const entry = { source: ENTRY_SOURCE.HISTORY, raw, receivedAtEpoch: Number(receivedAtEpoch || 0) };
  const { state: appended, appended: didAppend } = appendEntryIfNew(state, entry);
  if (!didAppend) { return state; }
  const next = appended;
  // History never touches turnInFlight — only the shared
  // permission/result clearing applies, with the lenient
  // (clear-on-empty) matching policy.
  applyPermissionTransition(next, raw, state, { strict: false });
  return next;
}

function pendingRequestId(pending) {
  if (!pending) { return ''; }
  return String(
    pending.request_id
    || pending.request?.request_id
    || pending.id
    || '',
  );
}

// Unwrap an SSE ``MessageEvent`` into ``{ envelope, raw }``. Both the
// live ``session_event`` and replayed ``session_history_event`` streams
// share this shape: a JSON body, an optional ``{ event }`` wrapper, and
// a ``{ raw }`` payload. Returns ``null`` when there's no usable raw
// event so callers can bail with a single guard.
function unwrapSessionEvent(event) {
  const payload = safeParseJSON(event.data);
  const envelope = payload?.event || payload;
  const raw = envelope?.raw || envelope;
  if (!raw) { return null; }
  return { envelope, raw };
}

export function useSessionStream(taskId, onIncomingEvent) {
  const [state, dispatch] = useReducer(
    reducer,
    taskId,
    (id) => readCachedState(id),
  );
  const [streamGeneration, setStreamGeneration] = useState(0);
  const taskIdRef = useRef(taskId);
  const idleRetryRef = useRef(IDLE_RETRY_MIN_MS);

  // Persist every state transition into the module-level cache so a
  // remount (tab switch) sees the latest events when it hydrates.
  useEffect(() => {
    if (state && taskIdRef.current) {
      writeCachedState(taskIdRef.current, state);
    }
  }, [state]);

  useEffect(() => {
    if (!taskId) { return undefined; }
    // Hydrate the reducer from the cache when taskId changes (or on
    // first mount). This is what restores pre-existing entries before
    // the new SSE connection starts replaying — without it, a remount
    // would render an empty list until the server's history catches
    // up.
    taskIdRef.current = taskId;
    // When the cache says we were STREAMING (or IDLE), preserve
    // that lifecycle through the re-open. The SSE side will refresh
    // it as soon as a new event lands. Forcing CONNECTING here
    // showed a misleading "Connecting…" banner on every send (the
    // sendMessage handler calls ``reconnect()`` after a respawn) —
    // operator saw a flicker even though the session itself was
    // still live.
    const cached = readCachedState(taskId);
    const carriedLifecycle = (
      cached.lifecycle === SESSION_LIFECYCLE.STREAMING
      || cached.lifecycle === SESSION_LIFECYCLE.IDLE
    )
      ? cached.lifecycle
      : SESSION_LIFECYCLE.CONNECTING;
    dispatch({
      // Reset the activity clock to NOW on (re)hydrate. The cached
      // ``lastEventAt`` is when the OPERATOR last watched this tab, not when
      // the session last acted — events keep flowing on the backend while you
      // view another tab. Carrying the stale value made the WorkingIndicator
      // flash "idle for Xm / may be stalled" for the ~1s until the backlog
      // replayed and refreshed it. Starting fresh means a tab switch never
      // shows phantom silence; a genuinely stalled session still trips the
      // warning once it's been quiet for the threshold AFTER the switch.
      type: ACTION_HYDRATE,
      value: { ...cached, lifecycle: carriedLifecycle, lastEventAt: Date.now() },
    });

    const stream = new EventSource(
      `/api/sessions/${encodeURIComponent(taskId)}/events`,
    );

    stream.addEventListener('session_event', (event) => {
      const unwrapped = unwrapSessionEvent(event);
      if (!unwrapped) { return; }
      const { envelope, raw } = unwrapped;
      dispatch({
        type: ACTION_INCOMING_EVENT,
        event: raw,
        receivedAtEpoch: envelope?.received_at_epoch,
      });
      if (typeof onIncomingEvent === 'function') {
        onIncomingEvent(raw, taskId);
      }
    });
    stream.addEventListener('session_history_event', (event) => {
      const unwrapped = unwrapSessionEvent(event);
      if (!unwrapped) { return; }
      dispatch({
        type: ACTION_INCOMING_HISTORY,
        event: unwrapped.raw,
        receivedAtEpoch: unwrapped.envelope?.received_at_epoch,
      });
    });
    stream.addEventListener('session_idle', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.IDLE });
      stream.close();
    });
    stream.addEventListener('session_missing', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.MISSING });
      stream.close();
    });
    stream.addEventListener('session_closed', () => {
      dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        dispatch({ type: ACTION_LIFECYCLE, value: SESSION_LIFECYCLE.CLOSED });
      }
    };
    return () => stream.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, streamGeneration]);

  // Fresh task → fresh backoff, so switching to a tab never inherits the
  // slow steady-state cadence the previous one had settled into.
  useEffect(() => { idleRetryRef.current = IDLE_RETRY_MIN_MS; }, [taskId]);

  // An idle tab is not a dead tab. ``session_idle`` closes the stream — there
  // is no subprocess to follow — and nothing reopened it, so the client went
  // permanently deaf the moment it saw one. Starting a task from the UI hit
  // this every time: the tab connected while the workspace was still being
  // provisioned (no agent session id yet, so no history to replay either),
  // got ``session_idle``, and closed. Kato then ran the whole task into a
  // chat that stayed empty behind "(no live subprocess for this tab)" — only
  // switching to another task and back, which remounts and reconnects,
  // revealed the transcript.
  //
  // Reconnecting does NOT resurrect anything: the server's idle path spawns a
  // session only when there is already queued work for it. This is a listener
  // waking up, not the lazy-resume design being undone.
  useEffect(() => {
    if (state.lifecycle !== SESSION_LIFECYCLE.IDLE) {
      idleRetryRef.current = IDLE_RETRY_MIN_MS;
      return undefined;
    }
    const delay = idleRetryRef.current;
    idleRetryRef.current = Math.min(delay * 2, IDLE_RETRY_MAX_MS);
    const handle = window.setTimeout(
      () => setStreamGeneration((n) => n + 1),
      delay,
    );
    return () => window.clearTimeout(handle);
    // ``streamGeneration`` is a dependency so a reconnect that lands on IDLE
    // again schedules the NEXT (longer) retry — without it the effect would
    // see an unchanged lifecycle and never re-arm.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.lifecycle, streamGeneration, taskId]);

  return {
    events: state.events,
    lifecycle: state.lifecycle,
    turnInFlight: state.turnInFlight,
    awaitingBackground: state.awaitingBackground,
    backgroundIsWorkflow: state.backgroundIsWorkflow,
    pendingPermission: state.pendingPermission,
    lastEventAt: state.lastEventAt,
    // Stamp the wall-clock at append (epoch SECONDS, matching the server's
    // ``received_at_epoch``) so an optimistic local prompt shows its time —
    // the operator's own prompts are LOCAL entries, not server-echoed.
    appendLocalEvent: (event) => dispatch({
      type: ACTION_LOCAL_EVENT,
      event: { receivedAtEpoch: Date.now() / 1000, ...event },
    }),
    markTurnBusy: (value) => dispatch({ type: ACTION_MARK_TURN_BUSY, value }),
    dismissPermission: () => dispatch({ type: ACTION_DISMISS_PERMISSION }),
    reconnect: () => setStreamGeneration((n) => n + 1),
    // The operator started a fresh chat / switched chats: drop the cached
    // transcript AND the in-memory one, then reconnect — the SSE replays
    // the (new) active session's history into the clean slate. A bare
    // reconnect() isn't enough: hydrate would restore the old chat's
    // bubbles from the cache and the new chat would render on top of them.
    resetChat: () => {
      clearTaskStreamCache(taskIdRef.current);
      dispatch({ type: ACTION_HYDRATE, value: emptyTaskState() });
      setStreamGeneration((n) => n + 1);
    },
  };
}

// Drop the cached chat state for a task — used when the operator
// "forgets" the workspace. Future mounts for that task start fresh.
export function clearTaskStreamCache(taskId) {
  if (!taskId) {
    TASK_STREAM_CACHE.clear();
    return;
  }
  TASK_STREAM_CACHE.delete(taskId);
}
