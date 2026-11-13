# opencode-core-lib

**OpenCode** CLI (`opencode`) agent backend. Implements
`agent_provider_contracts.AgentProvider` (one-shot runs) and
`AgentSession` / `AgentSessionManager` (chats), so a host drives OpenCode
through the same contract as every other backend.

It is a **mirror** of `claude_core_lib` and `codex_core_lib`: the same files,
the same public names, the same signatures, with only the agent's name changed
(`OpenCodeCliClient`, `StreamingOpenCodeSession`, `OpenCodeSessionManager`).
The layout is the contract in `agent_provider_contracts/cli_transport_layout.py`,
enforced across all four CLI transports by the host's
`tests/test_transport_api_parity.py`. Its one declared extra module is
`helpers/cli_generation.py` (see below).

## What lives here

```
opencode_core_lib/opencode_core_lib/
├── cli_client.py             ← OpenCodeCliClient: version probe, spawn, smoke test, argv
├── helpers/
│   ├── cli_generation.py     ← V1 (`opencode-ai`) or V2 (`@opencode/cli`)?
│   ├── effort_levels.py      ← model variants offered as effort levels
│   ├── model_catalog.py      ← models, read from `opencode models`
│   └── one_shot_utils.py     ← text in, text out (read-only rules)
└── session/
    ├── wire_protocol.py      ← the CLI's events → the shared vocabulary; argv + env
    ├── streaming.py          ← StreamingOpenCodeSession: one `opencode run` per turn
    ├── manager.py            ← OpenCodeSessionManager
    ├── history.py            ← replay a chat through `opencode session export`
    └── index.py              ← list sessions through `opencode session list`
```

Everything a per-turn CLI shares lives in `agent_core_lib`
(`session/per_turn_session.py`, `session/per_turn_manager.py`,
`session/turn_result.py`, `per_turn_cli_shared.py`); this lib holds only
OpenCode's argv, environment, event shapes, and the spawns.

## Two generations

OpenCode ships two maintained generations behind the same `opencode` binary.
`helpers/cli_generation.py` decides which is installed from `opencode
--version` (an unreadable version counts as V2: on V1 that fails loudly, while
the opposite mistake would silently drop the permission rules):

| | V1 (`opencode-ai`) | V2 (`@opencode/cli`) |
|---|---|---|
| server | in-process | shared background service, so runs pass **`--standalone`** (else the service ignores the run's environment, where the rules are) |
| effort | `--variant <name>` | `-m provider/model#<name>` |
| export | `opencode export <id>` | `opencode session export <id>` |
| error line | `{error: {name, data: {message}}}` | `{error: {type, message}}` |

## Differences from Claude and Codex

Read from OpenCode's published source (`github.com/anomalyco/opencode`, V2
`packages/cli/src/run/*`, V1 `packages/opencode/src/cli/cmd/run.ts`).

| Aspect | Claude | Codex | OpenCode |
|---|---|---|---|
| Non-interactive entry | `claude -p` | `codex exec` | `opencode run` |
| Prompt delivery | stdin | stdin | stdin (no message argument → all of stdin) |
| Event stream | `--output-format stream-json` | `--json` | `--format json` — **no done event**: exit 0 with no `error` line is success, so a clean exit completes the turn |
| Session resume | `--resume <id>` | `exec resume <id>` | `--session <id>` |
| Working directory | process cwd | `-C <dir>` | **`$PWD`** (read before the process's own directory); no `--add-dir` (several repositories → the folder that holds them all) |
| Approvals | permission mode + tool lists | sandbox mode | no OS sandbox and no flags: rules go inline in **`OPENCODE_CONFIG_CONTENT`** — edits allowed, the command floor denied, `external_directory` denied. With no terminal an `ask` is auto-rejected |
| Strict git | deny flags | prompt only | **shell rules per turn** (`"git push *": "deny"` …) — enforced, not only asked |
| System prompt | `--append-system-prompt` | none (spliced into the prompt) | none (spliced into the prompt) |
| Max turns / tool lists | flags | none | none |
| Self-update | — | — | off (`OPENCODE_DISABLE_AUTOUPDATE=1`) |
| Docker sandbox / bypass | supported | supported | **refused** — no sandbox image exists for OpenCode, so these fail loudly instead of running unsandboxed |

## Install

```bash
npm install -g @opencode/cli                     # V2
npm install -g opencode-ai                       # V1
curl -fsSL https://opencode.ai/v2/install | bash
opencode auth login                              # connect a provider
```

## Verified, and not

- **From source:** the `run` flags, the JSON envelope and every event payload,
  the missing done event, stdin and `$PWD` handling, the auto-reject of
  approvals, `--standalone`, and the generation differences above.
- **Documented only:** `session list --format json` / `session export` output
  shapes read by `index.py` and `history.py`; both readers answer empty for
  anything they do not recognise.
- **Not run against a live CLI** (no `opencode` binary was available while this
  lib was written). The tests drive a fake `opencode` that parses the real argv.

## Tests

```bash
python -m unittest discover -s opencode_core_lib/opencode_core_lib/tests -t .
```
