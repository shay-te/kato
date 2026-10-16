"""How many tokens a Claude conversation CONSUMED, read from its transcript.

``context_window`` answers "how full is the window right now" from the last
turn. This answers the other question — "what has this conversation cost in
total" — by adding up every API call the transcript recorded.

Three things make a naive sum wrong, and each was measured on a real 185 MB
transcript:

* **One API message is written as several lines.** Claude Code logs a line per
  content block (text, each tool call, ...) and repeats the message's whole
  ``usage`` on every one of them. That transcript held 7,169 usage lines for
  3,026 messages (up to 15 repeats each), so adding the lines overstates the
  total ~2.4x. Messages are therefore counted ONCE, by ``message.id``.
* **A re-read must be cheap.** The transcript of a live chat is appended to
  every few seconds and reaches hundreds of MB. :class:`TranscriptUsageReader`
  remembers how far it has read and parses only the bytes added since, instead
  of walking the whole file again on every request.
* **Cache reads dwarf everything else.** Each turn re-reads the whole context,
  so ``cache_read`` is usually >90% of the raw total. It is reported as its own
  field, never folded into ``input``, so a reader can see what a task actually
  wrote to the model versus what it merely re-read.
* **Tokens are not what a task cost.** The three groups are priced very
  differently (a cache read is a small fraction of an output token), so each
  message is also priced at its own model's published rates and the dollars are
  kept per model and in the same three groups. A model with no price on file is
  listed in ``unpriced_models`` — never counted as free.

Pure I/O with no host types, so it stays trivially testable.
"""

from __future__ import annotations

import json
import os
import threading

from agent_core_lib.agent_core_lib.helpers.context_accounting import sum_usage_tokens
from claude_core_lib.claude_core_lib.helpers.model_pricing import (
    add_cost,
    empty_cost,
    is_priced,
    message_cost,
    rounded_cost,
)

# transcript ``usage`` key -> the short name the totals use.
_USAGE_FIELDS = (
    ('input_tokens', 'input'),
    ('output_tokens', 'output'),
    ('cache_read_input_tokens', 'cache_read'),
    ('cache_creation_input_tokens', 'cache_write'),
)
COUNT_NAMES = tuple(name for _, name in _USAGE_FIELDS)

# Written by the CLI for turns it synthesised itself (an interrupted request, a
# local command). Not an API call, carries zeros, and must not show up as a model.
_SYNTHETIC_MODEL = '<synthetic>'

# How much of the file's start identifies it. If this changes the file was
# replaced, not appended to, and what was read before no longer applies.
_HEAD_BYTES = 256


def empty_usage() -> dict:
    """A totals record with nothing counted yet."""
    return {
        **{name: 0 for name in COUNT_NAMES},
        'total': 0,
        'turns': 0,
        'first_at': '',
        'last_at': '',
        'cost': empty_cost(),
        'unpriced_models': [],
        'models': {},
    }


def add_usage(into: dict, other: dict) -> dict:
    """Add the totals ``other`` to ``into`` (in place) and return ``into``.

    Used to combine the several transcripts one task owns (a fresh chat, an
    earlier chat that was set aside) into the task's figure.
    """
    for name in (*COUNT_NAMES, 'total', 'turns'):
        into[name] += int(other.get(name, 0) or 0)
    into['first_at'] = _earliest(into['first_at'], str(other.get('first_at', '') or ''))
    into['last_at'] = _latest(into['last_at'], str(other.get('last_at', '') or ''))
    add_cost(into['cost'], other.get('cost'))
    into['unpriced_models'] = sorted({*into['unpriced_models'], *(other.get('unpriced_models') or [])})
    for model, counts in (other.get('models') or {}).items():
        slot = into['models'].setdefault(model, _empty_model_usage(model))
        for name in (*COUNT_NAMES, 'total', 'turns'):
            slot[name] += int(counts.get(name, 0) or 0)
        slot['first_at'] = _earliest(slot['first_at'], str(counts.get('first_at', '') or ''))
        slot['last_at'] = _latest(slot['last_at'], str(counts.get('last_at', '') or ''))
        add_cost(slot['cost'], counts.get('cost'))
    return into


class TranscriptUsageReader:
    """Totals the usage of transcript files, parsing each byte only once.

    One instance is meant to live for the life of the process: the per-file
    state it keeps IS the cache. Safe to call from several threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, _FileState] = {}

    def read(self, path: str | os.PathLike) -> dict:
        """The usage totals for the transcript at ``path`` (zeros when unreadable)."""
        key = str(path)
        with self._lock:
            state = self._files.get(key)
            try:
                state = self._advance(key, state)
            except OSError:
                self._files.pop(key, None)
                return empty_usage()
            return state.totals()

    def _advance(self, key: str, state: '_FileState | None') -> '_FileState':
        info = os.stat(key)
        head = _read_head(key)
        if (
            state is None
            or state.inode != info.st_ino
            or state.head != head
            or info.st_size < state.offset
        ):
            # New to us, replaced, or shorter than what we already consumed:
            # start over rather than trust numbers from a different file.
            state = _FileState(inode=info.st_ino, head=head)
            self._files[key] = state
        if info.st_size > state.offset:
            state.consume(key)
        return state


class _FileState:
    """What has been read of one transcript so far."""

    def __init__(self, *, inode: int, head: bytes) -> None:
        self.inode = inode
        self.head = head
        # Bytes consumed — always the end of a COMPLETE line, so a line the CLI
        # is midway through writing is picked up whole on the next read.
        self.offset = 0
        # message id -> {model, counts, at}. Last write wins per field-maximum.
        self.messages: dict[str, dict] = {}

    def consume(self, path: str) -> None:
        with open(path, 'rb') as handle:
            handle.seek(self.offset)
            for line in handle:
                if not line.endswith(b'\n'):
                    break
                self.offset += len(line)
                self._take(line, position=self.offset)

    def _take(self, line: bytes, *, position: int) -> None:
        # Most lines (file snapshots, titles, user turns) carry no usage; skip
        # them without paying for a JSON parse.
        if b'"usage"' not in line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict) or event.get('type') != 'assistant':
            return
        message = event.get('message')
        if not isinstance(message, dict):
            return
        usage = message.get('usage')
        model = str(message.get('model') or '')
        if not isinstance(usage, dict) or model == _SYNTHETIC_MODEL:
            return
        counts = {
            name: sum_usage_tokens(usage, (key,)) for key, name in _USAGE_FIELDS
        }
        # A message without an id cannot be de-duplicated; give it a key of its
        # own (its position) so it is counted once rather than merged into
        # another id-less message.
        message_id = str(message.get('id') or f'@{position}')
        # How many of the written tokens were a 1-hour cache entry (priced at
        # twice the input rate, against 1.25x for the default 5 minutes). Not a
        # counter of its own: it is part of ``cache_write``, so adding it to the
        # total would count those tokens twice.
        write_1h = sum_usage_tokens(
            usage.get('cache_creation'), ('ephemeral_1h_input_tokens',),
        )
        at = str(event.get('timestamp') or '')
        seen = self.messages.get(message_id)
        if seen is None:
            self.messages[message_id] = {
                'model': model, 'counts': counts, 'at': at, 'write_1h': write_1h,
            }
            return
        # A repeat of the same API message: usage only ever grows as the reply
        # streams in, so the larger figure per field is the final one.
        for name in COUNT_NAMES:
            seen['counts'][name] = max(seen['counts'][name], counts[name])
        seen['write_1h'] = max(seen['write_1h'], write_1h)
        seen['at'] = _latest(seen['at'], at)

    def totals(self) -> dict:
        usage = empty_usage()
        unpriced: set[str] = set()
        for message in self.messages.values():
            counts = message['counts']
            total = sum(counts.values())
            for name in COUNT_NAMES:
                usage[name] += counts[name]
            usage['total'] += total
            usage['turns'] += 1
            usage['first_at'] = _earliest(usage['first_at'], message['at'])
            usage['last_at'] = _latest(usage['last_at'], message['at'])
            slot = usage['models'].setdefault(
                message['model'], _empty_model_usage(message['model']),
            )
            for name in COUNT_NAMES:
                slot[name] += counts[name]
            slot['total'] += total
            slot['turns'] += 1
            slot['first_at'] = _earliest(slot['first_at'], message['at'])
            slot['last_at'] = _latest(slot['last_at'], message['at'])
            # Priced per message: a rate can depend on that request's prompt
            # length, which no total can reconstruct.
            cost = message_cost(message['model'], counts, message['write_1h'])
            if cost is None:
                unpriced.add(message['model'])
            else:
                add_cost(slot['cost'], cost)
                add_cost(usage['cost'], cost)
        usage['unpriced_models'] = sorted(unpriced)
        usage['cost'] = rounded_cost(usage['cost'])
        for slot in usage['models'].values():
            slot['cost'] = rounded_cost(slot['cost'])
        return usage


def _empty_model_usage(model: str) -> dict:
    return {
        **dict.fromkeys(COUNT_NAMES, 0),
        'total': 0,
        'turns': 0,
        'first_at': '',
        'last_at': '',
        'priced': is_priced(model),
        'cost': empty_cost(),
    }


def _read_head(path: str) -> bytes:
    with open(path, 'rb') as handle:
        return handle.read(_HEAD_BYTES)


def _earliest(left: str, right: str) -> str:
    """The earlier of two ISO timestamps; a blank one never wins."""
    if not left or not right:
        return left or right
    return min(left, right)


def _latest(left: str, right: str) -> str:
    """The later of two ISO timestamps; a blank one never wins."""
    return max(left, right)
