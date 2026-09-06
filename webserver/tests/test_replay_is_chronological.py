"""Replay is ONE ordered stream, not three concatenated ones.

The chat's history came from three sources: the workspace preflight log, the
agent CLI's own transcript, and kato's session events (Action Guard blocks,
out-of-folder warnings, permission asks). They were emitted back to back — the
whole conversation, and only THEN every kato-side event.

So a block recorded in the middle of a turn replayed at the very BOTTOM of the
chat, under the operator's newest message, looking like it had just fired:
"why showing me old messages at the bottom. show them in their place."

The fix is not special handling for those messages. They are ordinary events
with an ordinary timestamp, and the replay merges every source oldest-first —
which is why this file tests ORDER, and never mentions a message kind.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from kato_webserver import app as app_module


class _Event:
    """A kato session event, at a known time."""

    def __init__(self, epoch: float, marker: str) -> None:
        self.received_at_epoch = epoch
        self.event_type = 'system'
        self.raw = {'type': 'system', 'marker': marker}

    def to_dict(self):
        return {'raw': self.raw, 'received_at_epoch': self.received_at_epoch}


class _Session:
    """A session whose subprocess has already exited.

    The live tail is not what this file is about: it closes at once so the
    frames observed are exactly the replay.
    """

    is_alive = False

    def __init__(self, events):
        self._events = events

    def recent_events(self):
        return list(self._events)

    def events_after(self, index):
        return [], index


class _Workspace:
    def __init__(self, entries):
        self._entries = entries

    def read_preflight_log(self, task_id):  # noqa: ARG002
        return list(self._entries)

    def get(self, task_id):  # noqa: ARG002
        # A workspace exists for this task — the route needs one before it
        # will replay anything.
        return SimpleNamespace(task_id='T1', repository_ids=[])


def _markers(frames):
    """The ``marker``/``timestamp`` of each frame, in emitted order."""
    out = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith('data: '):
                continue
            payload = json.loads(line[len('data: '):])
            raw = (payload.get('event') or {}).get('raw') or {}
            marker = raw.get('marker') or raw.get('message')
            if marker:
                out.append(marker)
    return out


class ReplayIsChronologicalTests(unittest.TestCase):
    def _replay(self, *, preflight, history, backlog):
        """Drive the REAL route generator.

        Deliberately not a local ``heapq.merge`` over the three sources: the
        first version of this file did exactly that, and it passed happily
        with the production code concatenating the streams — it was testing
        ``heapq``, not kato. The ordering has to be observed where it is
        actually produced.
        """
        session = _Session(backlog)
        manager = SimpleNamespace(
            get_session=lambda task_id: session,
            get_record=lambda task_id: SimpleNamespace(
                task_id='T1', agent_backend='claude', agent_session_id='sid-1',
            ),
        )
        original = app_module._replay_history
        app_module._replay_history = lambda record, sid: iter(history)
        try:
            frames = list(app_module._event_stream_generator(
                manager, _Workspace(preflight), 'T1',
            ))
        finally:
            app_module._replay_history = original
        return _markers(frames)

    def test_a_kato_event_lands_between_the_turns_it_happened_between(self) -> None:
        # THE REPORT. The kato event at t=20 belongs in the middle, not last.
        order = self._replay(
            preflight=[],
            history=[
                (10.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 10.0,
                               'raw': {'type': 'user', 'marker': 'turn-1'}}},
                )),
                (30.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 30.0,
                               'raw': {'type': 'user', 'marker': 'turn-2'}}},
                )),
            ],
            backlog=[_Event(20.0, 'kato-event')],
        )
        self.assertEqual(order, ['turn-1', 'kato-event', 'turn-2'])

    def test_an_old_kato_event_is_not_pushed_to_the_bottom(self) -> None:
        # The precise complaint: it fired FIRST but rendered LAST.
        order = self._replay(
            preflight=[],
            history=[
                (50.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 50.0,
                               'raw': {'type': 'user', 'marker': 'newest-turn'}}},
                )),
            ],
            backlog=[_Event(5.0, 'old-kato-event')],
        )
        self.assertEqual(order, ['old-kato-event', 'newest-turn'])
        self.assertNotEqual(
            order[-1], 'old-kato-event',
            'an old event replayed under the newest message',
        )

    def test_preflight_sorts_by_its_own_time_not_to_the_top(self) -> None:
        # Preflight used to ship a placeholder epoch of 0, which would sort
        # every clone line above the whole conversation.
        order = self._replay(
            preflight=[(25.0, 'cloning 1/1: form-core-lib')],
            history=[
                (10.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 10.0,
                               'raw': {'type': 'user', 'marker': 'before'}}},
                )),
                (40.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 40.0,
                               'raw': {'type': 'user', 'marker': 'after'}}},
                )),
            ],
            backlog=[],
        )
        self.assertEqual(order, ['before', 'cloning 1/1: form-core-lib', 'after'])

    def test_preflight_carries_its_real_time_on_the_wire(self) -> None:
        pairs = list(app_module._replay_preflight_log(
            _Workspace([(7.0, 'cloning 1/1: x')]), 'T1',
        ))
        self.assertEqual([epoch for epoch, _f in pairs], [7.0])
        payload = json.loads(
            [ln for ln in pairs[0][1].splitlines()
             if ln.startswith('data: ')][0][len('data: '):]
        )
        self.assertEqual(payload['event']['received_at_epoch'], 7.0)

    def test_every_source_is_emitted_exactly_once(self) -> None:
        # Merging must not drop or duplicate anything.
        order = self._replay(
            preflight=[(1.0, 'p1'), (4.0, 'p2')],
            history=[
                (2.0, app_module._sse_message(
                    'session_history_event',
                    {'event': {'received_at_epoch': 2.0,
                               'raw': {'type': 'user', 'marker': 'h1'}}},
                )),
            ],
            backlog=[_Event(3.0, 'k1'), _Event(5.0, 'k2')],
        )
        self.assertEqual(order, ['p1', 'h1', 'k1', 'p2', 'k2'])


if __name__ == '__main__':
    unittest.main()
