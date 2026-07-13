# Kato desktop

Electron wrapper that launches kato and opens the planning UI in a
native desktop window. **Optional** — `./kato up` from a terminal
keeps working exactly as before; this folder just adds a
double-clickable alternative.

## What it does

1. On launch, spawns `./kato up` (the existing bash wrapper at the
   repo root) as a child process.
2. Polls `https://127.0.0.1:5050/` until the Flask webserver kato
   boots inside its own process is reachable (up to 60s). kato serves
   HTTPS by default with a certificate signed by its own local CA (a
   loopback address can't get one from a real public CA). On macOS
   kato installs that CA into your login Keychain on first run (a
   one-time Keychain prompt), after which Electron trusts the
   certificate the normal way; as a fallback the launcher also pins
   the exact leaf certificate by fingerprint itself, never a blanket
   "skip verification" — see `KATO_WEBSERVER_HTTPS` below to opt out.
3. Opens a BrowserWindow on that URL. The Electron process owns
   the window; kato owns the orchestrator + webserver.
4. When the window closes, sends SIGTERM to the kato child so the
   port doesn't stay bound.

External links (PR URLs, ticket URLs) open in the operator's real
browser instead of inside the window.

## Run from source

```bash
cd desktop
npm install
npm start
```

`npm install` pulls Electron (~250 MB) into `node_modules/`.

The launcher reuses your existing kato install — `pip install -e .`
must have been run from the repo root at least once, and any
secrets in `<repo>/.env` apply unchanged.

## Build a redistributable app

`electron-builder` is wired in `package.json`. To produce a
`.dmg` (macOS), `.exe` installer (Windows), or `.AppImage`
(Linux):

```bash
cd desktop
npm install
npm run build
# Output lands in desktop/dist/
```

The build copies the repo's Python source into
`Contents/Resources/kato-repo` (see `extraResources` in
`package.json`) so the launcher can find `./kato` at runtime. The
target machine still needs **Python 3.11+** and `pip install` of
kato's dependencies — the bundle is the launcher + the kato source
tree, not a frozen Python runtime. Adding PyInstaller or
`python-build-standalone` to produce a fully sealed binary is a
follow-up.

## Configuration

The launcher honours the same env vars as the CLI flow:

| Env var | Default | Meaning |
|---|---|---|
| `KATO_WEBSERVER_HOST` | `127.0.0.1` | Address kato binds + Electron loads |
| `KATO_WEBSERVER_PORT` | `5050`      | Port for the same |
| `KATO_WEBSERVER_HTTPS` | `1` (on) | Serve/load over HTTPS with a self-signed cert. Set `0`/`false` to use plain HTTP instead (both kato and the launcher must agree — set it before spawning either). |
| `KATO_TLS_DIR` | `~/.kato/tls` | Where the self-signed cert/key live — the launcher reads the same path to know which certificate to trust. |
| `KATO_WEBSERVER_DISABLED` | unset | If set, kato won't start the webserver — the launcher won't be able to reach it and will error out |

Put these in `<repo>/.env` (the existing kato file) so both the
desktop launcher and `./kato up` see the same values.

## Troubleshooting

* **"Kato failed to start" dialog** — read the last 2 KB of kato's
  stdout/stderr in the dialog, then run `./kato up` directly from
  a terminal to see the full log. The launcher's healthcheck times
  out at 60s.
* **"port already in use"** — a previous kato is still alive. Run
  `pkill -f 'kato_core_lib.main'` (or close the existing
  `./kato up` terminal) and relaunch.
* **Browser shows the planning UI but nothing else** — the kato
  scan loop hasn't found tasks yet. Wait one scan cycle (default
  30s) or click "Refresh" in the top bar.

## Why not bundle Python too?

Kato pulls in hydra-core, docker, git tooling, and per-OS sandbox
glue. Freezing all of that into a single binary (PyInstaller,
shiv, py2app) works for greenfield apps; for kato it would mean
re-validating sandbox + Docker paths every release. We chose the
"launcher" pattern instead — small to ship, easy to debug.
