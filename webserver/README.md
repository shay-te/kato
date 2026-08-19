# Kato webserver

A small Flask app that will host the Kato planning UI: one tab per in-flight
Kato task, each connected to the Claude Code CLI session bound to that task,
so a human can chat with the agent, refine a plan, and approve permission
prompts.

This first revision is a skeleton. It exposes the route surface and an
in-memory session registry; the streaming, subprocess, and ticket-state-driven
tab lifecycle land in follow-up changes.

## Why a separate folder

The planning UI is its own deployable: different runtime (Flask), different
dependency surface (`flask`, `flask-sock`), different lifecycle. Keeping it out
of the `kato` package avoids dragging web framework deps into the unattended
orchestrator.

## Run locally

```bash
cd webserver
python -m pip install -e .
python -m kato_webserver.app
```

Then open https://localhost:5050 . A loopback address can't get a
certificate from a real CA, so kato generates its own local CA (like
`mkcert`) plus a server cert signed by it, all persisted under
`~/.kato/tls/`. The leaf cert covers `localhost`, `127.0.0.1` and
`::1`, so any of the three works.

Kato then installs that CA into the **current user's** trust store on
first run, so the browser stops warning:

| OS | Where the CA goes | Needs admin? |
| --- | --- | --- |
| macOS | login Keychain (`security add-trusted-cert`) | No — one-time Keychain prompt |
| Windows | user `Root` store (`certutil -addstore -user`) | No |
| Linux | user NSS db (`certutil -d sql:~/.pki/nssdb`) — Chrome/Chromium/Firefox | No — needs `libnss3-tools` (`apt install libnss3-tools` / `dnf install nss-tools`) |

Never the machine-wide store: a local development CA that browsers
trust for every site on the box is not something to install with
elevation behind an operator's back.

If the install can't run (you decline the prompt, `certutil` is
missing, no NSS profile exists yet) kato logs the reason and the
browser falls back to the usual one-time "connection is not private"
click-through. The install is retried on each restart, and a trust
marker keeps it from re-prompting once it has succeeded for the
current CA.

Note that the click-through exception a browser remembers is **per
origin**: accepting it on `https://127.0.0.1:5050` does nothing for
`https://localhost:5050`. Trusting the CA is what fixes both.

## Configuration

| Variable | Default | What it does |
| --- | --- | --- |
| `KATO_WEBSERVER_HOST` | `127.0.0.1` | Bind address for the dev server. |
| `KATO_WEBSERVER_PORT` | `5050` | Bind port. |
| `KATO_WEBSERVER_HTTPS` | `1` (on) | Serve over HTTPS with a self-signed cert. Set `0`/`false` to serve plain HTTP instead. |
| `KATO_TLS_DIR` | `~/.kato/tls` | Where the self-signed cert/key are generated + persisted. |

## Endpoints (current)

| Route | Purpose |
| --- | --- |
| `GET /` | HTML page; renders one card per active planning session. |
| `GET /api/sessions` | JSON list of all sessions in the registry. |
| `GET /api/sessions/<task_id>` | JSON for one session (404 if not found). |
| `GET /healthz` | Liveness probe. |

## Planned next steps

1. Subprocess-backed Claude session manager (one `claude -p --output-format stream-json --input-format stream-json` per task).
2. WebSocket endpoint per task that streams events to/from the bound session.
3. Permission-prompt handling via the `--permission-prompt-tool` hook.
4. Tab lifecycle: appears when the task is picked up by Kato (or tagged `kato:wait-planning`); disappears when the ticket moves to done/completed or the PR is merged.
