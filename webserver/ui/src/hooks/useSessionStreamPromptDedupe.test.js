// One prompt, rendered once — however many sources replay it.
//
// A live chat is fed from TWO places at once: the transcript on disk
// (everything before this process attached) and the live SSE stream
// (everything since). Reconnecting, reopening a tab, or resuming a session
// replays the same logical prompt down both.
//
// The reducer already deduped across sources, but only on a "strong" id, and
// it checked ``uuid`` FIRST. Claude assigns a uuid per JSONL RECORD rather
// than per logical message, so the live copy and the replayed copy of one
// prompt carry different uuids — two identities, two bubbles. Assistant turns
// escaped it because they also carry an Anthropic ``message.id``, which IS
// stable across both sources. That is why only the PROMPT duplicated, which
// is exactly how it was reported.
//
// Driven through the exported reducer rather than the hook: this is the real
// dedupe path, and it needs no EventSource.

import test from 'node:test';
import assert from 'node:assert/strict';
import { reducer } from './useSessionStream.js';

function emptyState() {
  return {
    events: [],
    eventKeys: new Set(),
    lifecycle: 'streaming',
    turnInFlight: false,
    pendingPermission: null,
    lastEventAt: 0,
    streamGeneration: 0,
  };
}

function prompt(uuid, text, { asString = false } = {}) {
  return {
    type: 'user',
    uuid,
    message: { content: asString ? text : [{ type: 'text', text }] },
  };
}

function reply(uuid, id, text) {
  return {
    type: 'assistant',
    uuid,
    message: { id, content: [{ type: 'text', text }] },
  };
}

// The transcript replay arrives as history; the live event as a server event.
function replayThenLive(state, historyEvent, liveEvent) {
  let next = reducer(state, {
    type: 'incoming_history', event: historyEvent, receivedAtEpoch: 1,
  });
  next = reducer(next, {
    type: 'incoming_event', event: liveEvent, receivedAtEpoch: 2,
  });
  return next;
}

function userEvents(state) {
  return state.events.filter((entry) => entry?.raw?.type === 'user');
}

test('one prompt replayed from both sources renders once', () => {
  // THE BUG. Same prompt, different per-record uuids.
  const next = replayThenLive(
    emptyState(),
    prompt('jsonl-record-1', 'can you check the price?'),
    prompt('live-event-1', 'can you check the price?'),
  );
  assert.equal(userEvents(next).length, 1);
});

test('it also matches a prompt whose content is a plain string', () => {
  // Prompts arrive as a string on some paths and as blocks on others; the
  // string shape had no identity at all, so those still duplicated.
  const next = replayThenLive(
    emptyState(),
    prompt('a', 'same words', { asString: true }),
    prompt('b', 'same words', { asString: true }),
  );
  assert.equal(userEvents(next).length, 1);
});

test('a string-content copy matches a block-content copy', () => {
  // The two sources do not have to agree on the shape.
  const next = replayThenLive(
    emptyState(),
    prompt('a', 'same words', { asString: true }),
    prompt('b', 'same words'),
  );
  assert.equal(userEvents(next).length, 1);
});

test('two genuinely different prompts both survive', () => {
  const next = replayThenLive(
    emptyState(), prompt('a', 'first ask'), prompt('b', 'second ask'),
  );
  assert.equal(userEvents(next).length, 2);
});

test('an empty prompt is not treated as an identity', () => {
  // '' would otherwise match every other contentless user event and swallow
  // it, which is a far worse failure than a duplicate.
  const next = replayThenLive(emptyState(), prompt('a', ''), prompt('b', ''));
  assert.equal(userEvents(next).length, 2);
});

test('assistant turns are untouched: the uuid still decides', () => {
  // Non-regression, and a deliberate asymmetry worth stating.
  //
  // Assistant turns keep matching on ``uuid`` FIRST, which means two copies
  // carrying different uuids are both kept — verified identical before and
  // after this fix. It is tempting to prefer ``message.id`` here for the same
  // reason text is preferred for prompts, but the two cases are not alike: a
  // single assistant message legitimately emits SEVERAL events sharing one
  // ``message.id`` (a text block, then a tool_use block). Preferring it would
  // collapse those into one and DROP content — a much worse failure than a
  // duplicate, and not the bug that was reported.
  const differentUuids = replayThenLive(
    emptyState(),
    reply('r1', 'msg_1', 'the answer'),
    reply('r2', 'msg_1', 'the answer'),
  );
  assert.equal(
    differentUuids.events.filter((e) => e?.raw?.type === 'assistant').length, 2,
  );

  const sameUuid = replayThenLive(
    emptyState(),
    reply('r1', 'msg_1', 'the answer'),
    reply('r1', 'msg_1', 'the answer'),
  );
  assert.equal(
    sameUuid.events.filter((e) => e?.raw?.type === 'assistant').length, 1,
  );
});

test('distinct assistant turns are not collapsed', () => {
  const next = replayThenLive(
    emptyState(),
    reply('r1', 'msg_1', 'first answer'),
    reply('r2', 'msg_2', 'second answer'),
  );
  assert.equal(
    next.events.filter((e) => e?.raw?.type === 'assistant').length, 2,
  );
});

test('a tool RESULT keeps deduping on its tool_use_id', () => {
  // A tool result is ALSO delivered as a ``user`` envelope, but carries no
  // operator text — so routing every ``user`` event through the text
  // identity gave it an empty one and printed every tool result twice.
  const toolResult = { type: 'user', tool_use_id: 'toolu_9' };
  const next = replayThenLive(emptyState(), toolResult, toolResult);
  assert.equal(next.events.length, 1);
});

test('two different tool results both survive', () => {
  let next = reducer(emptyState(), {
    type: 'incoming_event',
    event: { type: 'user', tool_use_id: 'toolu_1' },
    receivedAtEpoch: 1,
  });
  next = reducer(next, {
    type: 'incoming_event',
    event: { type: 'user', tool_use_id: 'toolu_2' },
    receivedAtEpoch: 2,
  });
  assert.equal(next.events.length, 2);
});

// ---------------------------------------------------------------------------
// The reported case, which the cross-source fix above did NOT cover.
//
// The operator's own message is rendered immediately as a LOCAL echo so the
// composer feels responsive. The server replays that same message later as a
// ``user`` event — and on a reconnect it arrives inside a full history
// replay, far after the cached echo, with a whole agent turn in between.
//
// The only thing matching them was a display filter with a FOUR-entry
// lookback, so it caught the adjacent case and nothing else. That is why the
// duplicate always appeared with content between the two copies.
// ---------------------------------------------------------------------------

function localEcho(text) {
  return { source: 'local', kind: 'user', text };
}

function withLocalEcho(text) {
  return reducer(emptyState(), { type: 'local_event', event: localEcho(text) });
}

test('a replayed prompt does not duplicate the operator\u2019s own echo', () => {
  let state = withLocalEcho('can you check the price?');
  // A whole agent turn happens in between — this is the distance the old
  // 4-entry lookback could not span.
  for (let i = 0; i < 12; i += 1) {
    state = reducer(state, {
      type: 'incoming_event',
      event: { type: 'assistant', uuid: `a${i}`, message: { id: `m${i}`, content: [] } },
      receivedAtEpoch: i,
    });
  }
  state = reducer(state, {
    type: 'incoming_history',
    event: prompt('jsonl-1', 'can you check the price?'),
    receivedAtEpoch: 99,
  });
  const prompts = state.events.filter(
    (e) => e.source === 'local' || e?.raw?.type === 'user',
  );
  assert.equal(prompts.length, 1);
});

test('the echo survives a HYDRATE and still suppresses the replay', () => {
  // The reconnect path: the cached transcript is rehydrated, THEN the server
  // replays its history. If hydrate does not restore the echo's identity,
  // the replayed copy has nothing to match against.
  const withEcho = withLocalEcho('can you check the price?');
  let state = reducer(emptyState(), {
    type: 'hydrate', value: { ...withEcho, eventKeys: new Set() },
  });
  state = reducer(state, {
    type: 'incoming_history',
    event: prompt('jsonl-1', 'can you check the price?'),
    receivedAtEpoch: 99,
  });
  assert.equal(state.events.length, 1);
});

test('the operator sending the same words twice renders twice', () => {
  // The echo registers an identity but is never suppressed BY one: it is the
  // operator's own action and must always appear.
  let state = withLocalEcho('continue');
  state = reducer(state, { type: 'local_event', event: localEcho('continue') });
  assert.equal(state.events.length, 2);
});

test('a kato-injected prompt is still shown', () => {
  // Server ``user`` events that do NOT match something the operator typed are
  // real content — they explain why the agent suddenly started working.
  let state = withLocalEcho('my own message');
  state = reducer(state, {
    type: 'incoming_history',
    event: prompt('jsonl-1', 'Review the following comment...'),
    receivedAtEpoch: 5,
  });
  assert.equal(state.events.length, 2);
});
