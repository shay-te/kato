# Kato Desktop (Tauri)

A native desktop app that wraps Kato: a tiny **Tauri** (Rust + OS-webview) shell
that spawns a **bundled, frozen Python `kato` sidecar**, waits for its Flask
webserver, and opens the planning UI in its own window — with **built-in signed
auto-update**.

**Everything desktop/installer-related lives under this `desktop/` folder.**
Nothing here is imported by Kato; the build only *reads* the Kato repo as input.

> Reality check: Kato orchestrates host tools — **`git`, Docker, and an agent CLI
> (Claude/Codex/OpenHands) must be installed on the machine.** The app bundles the
> Python runtime (no Python/pip setup for the user); it does **not** — and cannot —
> bundle those external tools.

---

## Who creates the `.exe` / `.dmg` / `.AppImage`, and when?

**Not this repo checkout, and not automatically.** The installers are produced by
running `tauri build`, which requires the toolchains below **on a machine of each
target OS** (Tauri can't cross-compile installers). Two steps per OS:

1. **Freeze the sidecar** — PyInstaller packs `kato` into a standalone binary.
2. **`tauri build`** — compiles the Rust shell, embeds the sidecar + icons, and
   emits the platform installer.

| Target | Built on | Output |
|---|---|---|
| `.dmg` (+ `.app`) | **macOS** | `src-tauri/target/release/bundle/dmg/` |
| `.msi` / NSIS `.exe` | **Windows** | `src-tauri/target/release/bundle/{msi,nsis}/` |
| `.AppImage` / `.deb` | **Linux** | `src-tauri/target/release/bundle/{appimage,deb}/` |

**Recommended: let CI make them.** `ci/release.yml` is a GitHub Actions matrix
(macOS + Windows + Ubuntu runners) that builds the sidecar + runs `tauri build`
per OS on a version tag, and also emits the **updater feed**. Copy it to
`.github/workflows/desktop-release.yml` at the repo root to enable it (GitHub only
runs workflows from there — it's the one file that can't physically live under
`desktop/`). Then: **push a tag → CI produces all three installers + the update
feed.** Locally you can `npm run build` for *your own* OS to smoke-test.

---

## Prerequisites (only on machines that BUILD)

- **Rust** (stable) — `https://rustup.rs`
- **Node** ≥ 18 (for the Tauri CLI) — `npm install` here
- **Python 3.11** + Kato installed editable in a venv (`pip install -e .` at repo root)
- **PyInstaller** — `pip install pyinstaller`
- Platform bits: macOS Xcode CLT; Windows WebView2 (preinstalled on Win10/11) + MSVC; Linux `libwebkit2gtk-4.1-dev`, `build-essential`, etc.

## Build (local, your OS only)

```bash
cd desktop
npm install                 # Tauri CLI
npm run icon                # one-time: generate icons/* from icon.png
npm run sidecar             # PyInstaller → src-tauri/binaries/kato-sidecar-<triple>
npm run build               # tauri build → installer in src-tauri/target/release/bundle/
# dev loop (no installer): npm run dev
```

## Auto-update setup (once)

1. Generate a signing keypair: `npm run tauri signer generate -- -w ~/.kato-updater.key`
2. Put the **public** key in `src-tauri/tauri.conf.json` → `plugins.updater.pubkey`.
3. Set `plugins.updater.endpoints` to where you'll host the feed (S3 / GitHub
   Releases / any static host).
4. In CI, sign the build with the **private** key (`TAURI_SIGNING_PRIVATE_KEY`
   secret) and publish `latest.json` + the signed bundles. Installed apps then
   check → download → verify → **install on restart** (the VS-Code model).

## Bundle size (tuned light)

The spec + Rust profile are set for the smallest realistic bundle:
- **UI source maps stripped** (`*.map`, ~40 MB) — never shipped.
- **Optional security scanners excluded by default** (`INCLUDE_SECURITY_SCANNERS = False` in the spec) — each runner degrades gracefully if missing, so this is safe; ~20-40 MB saved. Flip to `True` to ship in-app scans.
- **The OpenHands backend + its dependency tree are excluded** (`_excludes` in the spec: `openhands_core_lib`, `botocore`, `sqlalchemy`, `redis`, `pymongo`, `neo4j`, `eventlet`, the test-only `moto`, …). OpenHands runs the agent in a docker-compose container stack the desktop app doesn't provide, so it is **not a usable backend from the desktop bundle** — use `kato up` / the CLI (or docker-compose) for OpenHands. The desktop app runs **Claude / Codex** (local CLIs). Also excluded: heavy transitive packages the Claude/Codex path never imports (`django`, `numpy`, `shapely`, `PIL`, `psycopg2`, `pandas`). This is the single biggest size + frozen-startup win — every bundled C-extension dylib is `dlopen`'d + signature-checked on launch.
- **Symbols stripped** (PyInstaller `strip=True`, Cargo `strip = true`) + **size-optimized Rust** (`opt-level="z"`, `lto`, `panic="abort"`) + Python `-OO`.

Realistic result (macOS, after the excludes above): the frozen sidecar is **~23 MB** and the `.app` **~28 MB**, dominated by the frozen Python + `cryptography`. UPX would shave more but **breaks macOS codesigning/notarization**, so it's off. The Rust shell + OS webview add only a few MB (no Chromium).

## Data continuity (shares state with your CLI)

The app runs the SAME frozen `kato up`, and the shell **does not override `HOME`/`KATO_HOME`** — so it reads/writes the SAME `~/.kato` (settings.json, tasks, workspaces) and `~/.claude` (sessions) as your terminal `kato`. **Nothing is lost**; the desktop app and CLI share one state dir.

Because a Finder/Dock launch doesn't load your shell profile, `src-tauri/src/main.rs` **augments `PATH`** (Homebrew + common bin dirs) so the app still finds `git` / Docker / the agent CLI. If you point kato at a custom home via a shell env var, pass it explicitly — a GUI launch won't inherit it.

## Layout

```
desktop/
├── icon.png                     source icon (→ tauri icon)
├── package.json                 Tauri CLI + scripts
├── sidecar/
│   ├── kato_sidecar.py          entry: runs `kato up`
│   ├── kato-sidecar.spec        PyInstaller spec (hidden-imports for lazy factories)
│   └── build-sidecar.sh         freeze → src-tauri/binaries/
├── src-tauri/
│   ├── Cargo.toml  build.rs  tauri.conf.json
│   ├── src/main.rs              spawn sidecar → wait for server → open window
│   ├── capabilities/default.json
│   ├── loading/index.html       splash shown until the server is up
│   ├── icons/                   generated (gitignored)
│   └── binaries/                sidecar output (gitignored)
└── ci/release.yml               GH Actions matrix (copy to .github/workflows/)
```
