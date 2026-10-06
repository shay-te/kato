"""A stand-in ``claude`` executable: the ONE thing kato tests fake.

Everything kato does around an agent — spawning it, the stream-json protocol,
sessions, turn detection, the read-only one-shot runs — is exercised for real
against this script. Only the model is fake: the script answers instead of
thinking.

Two modes, chosen the way the real CLI's callers choose them:

* **chat** (``--input-format stream-json``): prints an ``init`` event, then for
  every user message it reads, records it and answers with a finished turn.
  A message containing ``SLOW_TURN`` takes ``slow_seconds`` before its result,
  so a test can catch the chat mid-turn. A turn's final reply is ``fixed``,
  or the text a test scripted with ``script_chat_reply``. One containing
  ``NEEDS_APPROVAL``
  asks permission for a ``Write`` (a real ``control_request``) and finishes
  only once a ``control_response`` arrives on stdin — the operator's answer.
* **one-shot** (``-p --output-format json``): reads the prompt on stdin,
  records argv / cwd / prompt, and replies with the next scripted reply.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_SCRIPT = r'''#!{python}
import json, os, sys, time
ROOT = {root!r}
argv = sys.argv[1:]

def record(name, payload):
    with open(os.path.join(ROOT, name), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")

if "stream-json" in argv:
    sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else argv[argv.index("--resume") + 1]
    print(json.dumps({{"type": "system", "subtype": "init", "session_id": sid}}), flush=True)
    awaiting = None
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "control_response" and awaiting:
            awaiting = None
            print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "approved and written", "session_id": sid}}), flush=True)
            continue
        if message.get("type") != "user":
            continue
        content = message["message"]["content"]
        text = content if isinstance(content, str) else " ".join(
            block.get("text", "") for block in content if isinstance(block, dict))
        record("chat.jsonl", {{"text": text, "argv": argv, "cwd": os.getcwd()}})
        if "SLOW_TURN" in text:
            time.sleep({slow_seconds})
        if "NEEDS_APPROVAL" in text:
            awaiting = "req-write-1"
            print(json.dumps({{"type": "control_request", "request_id": awaiting, "request": {{"subtype": "can_use_tool", "tool_name": "Write", "input": {{"file_path": "notes.txt", "content": "x"}}}}}}), flush=True)
            continue
        reply_path = os.path.join(ROOT, "chat_reply.txt")
        final = open(reply_path, encoding="utf-8").read() if os.path.exists(reply_path) else "fixed"
        print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "on it"}}]}}}}), flush=True)
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": sid}}), flush=True)
else:
    prompt = sys.stdin.read()
    record("oneshot.jsonl", {{"prompt": prompt, "argv": argv, "cwd": os.getcwd()}})
    replies_path = os.path.join(ROOT, "replies.json")
    replies = json.load(open(replies_path)) if os.path.exists(replies_path) else []
    reply = replies.pop(0) if len(replies) > 1 else (replies[0] if replies else "ok")
    json.dump(replies, open(replies_path, "w"))
    print(json.dumps({{"type": "result", "result": reply, "is_error": False}}))
'''


class FakeClaudeCli(object):
    """Writes the script into ``root``; read back what it was sent."""

    def __init__(self, root: Path, *, slow_seconds: float = 1.5) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'claude'
        self.path.write_text(
            _SCRIPT.format(python=sys.executable, root=str(self.root), slow_seconds=slow_seconds),
            encoding='utf-8',
        )
        self.path.chmod(self.path.stat().st_mode | stat.S_IEXEC)

    @property
    def binary(self) -> str:
        return str(self.path)

    def script_replies(self, *replies: str) -> None:
        """One-shot replies, in order; the last one repeats."""
        (self.root / 'replies.json').write_text(json.dumps(list(replies)), encoding='utf-8')

    def script_chat_reply(self, text: str) -> None:
        """The final reply of every chat turn from now on."""
        (self.root / 'chat_reply.txt').write_text(text, encoding='utf-8')

    def chat_messages(self) -> list[dict]:
        return self._read('chat.jsonl')

    def oneshot_calls(self) -> list[dict]:
        return self._read('oneshot.jsonl')

    def _read(self, name: str) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def posix_only() -> bool:
    """The script relies on a shebang; Windows runs the CLI differently."""
    return os.name != 'nt'
