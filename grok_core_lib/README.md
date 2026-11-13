# grok-core-lib

xAI **Grok Build** CLI (`grok`) agent backend. Implements
`agent_provider_contracts.AgentProvider` (one-shot runs) and
`AgentSession` / `AgentSessionManager` (chats), so a host drives Grok through
the same contract as every other backend.

It is a **mirror** of `claude_core_lib` and `codex_core_lib`: the same files,
the same public names, the same signatures, with only the agent's name changed
(`GrokCliClient` / `CodexCliClient`, `StreamingGrokSession` /
`StreamingCodexSession`). The layout is the contract in
`agent_provider_contracts/cli_transport_layout.py`, enforced across all four
CLI transports by the host's `tests/test_transport_api_parity.py`.

## What lives here

```
grok_core_lib/grok_core_lib/
├── cli_client.py             ← GrokCliClient: version probe, spawn, smoke test, argv
├── helpers/
│   ├── effort_levels.py      ← --reasoning-effort levels, read from `grok --help`
│   ├── model_catalog.py      ← models, read from `grok models`
│   └── one_shot_utils.py     ← text in, text out (read-only sandbox)
└── session/
    ├── wire_protocol.py      ← the CLI's events → the shared vocabulary; argv helpers
    ├── streaming.py          ← StreamingGrokSession: one `grok -p` per turn
    ├── manager.py            ← GrokSessionManager
    ├── history.py            ← replay a chat from ~/.grok/sessions
    └── index.py              ← list sessions for adoption
```

Everything a per-turn CLI shares — the chat state machine, the manager, the
result reducer, the review-comment and investigation orchestration — lives in
`agent_core_lib` (`session/per_turn_session.py`, `session/per_turn_manager.py`,
`session/turn_result.py`, `per_turn_cli_shared.py`). This lib holds only
Grok's argv, its event shapes, and the spawns.

## The shared event vocabulary

Grok's events are translated (`session/wire_protocol.translate_event`) into
the per-turn vocabulary in `agent_core_lib/session/turn_events.py`
(`thread.started`, `item.completed` with `agent_message` / `command_execution`
/ `file_change` / …, `turn.completed`, `turn.failed`, `turn.aborted`). A host
draws a Grok chat with exactly the code it draws a Codex chat with.

## Differences from Claude and Codex

Read from Grok Build's published source (`github.com/xai-org/grok-build`,
`app/cli.rs`, `headless/reducer/*`) and its user guide.

| Aspect | Claude | Codex | Grok |
|---|---|---|---|
| Non-interactive entry | `claude -p` | `codex exec` | `grok -p` / **`--prompt-file <path>`** |
| Prompt delivery | stdin | stdin | **a file** — headless Grok never reads stdin |
| Event stream | `--output-format stream-json` | `--json` | `--output-format streaming-messages-json` (session id on the FIRST line, a `result` line ends every turn, failures included) |
| Session resume | `--resume <id>` | `exec resume <id>` | `--resume <id>` (keeps the session's own working directory) |
| Working directory | process cwd | `-C <dir>` | `--cwd <dir>`; no `--add-dir` (several repositories → the folder that holds them all) |
| Approvals | permission mode + tool lists | sandbox mode | `--always-approve` **inside** `--sandbox workspace` (Seatbelt / Landlock); no sandbox on Windows → `--permission-mode acceptEdits` only |
| Tool allow / deny | `--allowedTools` / `--disallowedTools` | none | `--allow` / `--deny` (Claude-style rules); the command floor is ALWAYS `--deny`, which beats every approval mode |
| Strict git | deny flags | prompt only | **`--deny` rules per turn** — enforced, not only asked |
| System prompt | `--append-system-prompt` | none (spliced into the prompt) | `--rules` |
| Effort | `--effort` | config key | `--reasoning-effort` |
| Max turns | `--max-turns` | none | `--max-turns` |
| Folder trust | — | — | `--trust` on every run (headless refuses an untrusted project that has rules or a `.grok` config) |
| Self-update | — | — | off (`--no-auto-update`, `GROK_DISABLE_AUTOUPDATER=1`) |
| Docker sandbox / bypass | supported | supported | **refused** — no sandbox image exists for Grok, so these fail loudly instead of running unsandboxed |

## Install

```bash
curl -fsSL https://x.ai/cli/install.sh | bash          # macOS / Linux / WSL
irm https://x.ai/cli/install.ps1 | iex                 # Windows PowerShell
grok login                                              # or set XAI_API_KEY
```

## Verified, and not

- **From source:** every flag spelling used here; the `streaming-messages-json`
  line shapes; that headless runs never read stdin and auto-cancel an approval
  they cannot ask for; `grok models` output; `--trust`.
- **Documented only:** the session store layout (`~/.grok/sessions/<cwd>/<id>/
  updates.jsonl`) that `history.py` and `index.py` read. Its line wrapper is
  not specified, so both readers accept the update at the top level or nested,
  and answer empty for anything else.
- **Not run against a live CLI** (no `grok` binary was available while this lib
  was written). The tests drive a fake `grok` that parses the real argv.

## Tests

```bash
python -m unittest discover -s grok_core_lib/grok_core_lib/tests -t .
```
