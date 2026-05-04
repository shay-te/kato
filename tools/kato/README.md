# `tools/kato/` — cross-platform `kato` entry point

Source for the operator command that mirrors the POSIX `Makefile`. The
canonical command is `kato <target>` on every platform:

| Platform | How it runs |
|---|---|
| macOS / Linux | `./kato up` — committed bash wrapper at the repo root invokes `python3 tools/kato/kato.py` |
| Windows       | `.\kato.exe up` — built once via `python tools\kato\build.py` (PyInstaller) |

## Files

| File | Purpose |
|---|---|
| `kato.py` | All dispatch logic. Mirrors every target in the root `Makefile`. Edit here when adding/renaming targets. |
| `build.py` | Packages `kato.py` into a single `kato.exe` using PyInstaller. Windows-only convenience. |

## Build the Windows binary

```powershell
# One-time, after bootstrap has created .venv\:
.\.venv\Scripts\python.exe tools\kato\build.py
```

Outputs `kato.exe` at the repo root (~8 MB, gitignored).

## Targets

```
kato up                  Start kato
kato bootstrap           Install Python deps + build the planning UI
kato configure           Generate .env interactively
kato doctor              Validate full env config
kato doctor-agent        Validate just the agent backend
kato doctor-openhands    Validate just the openhands config
kato test                Run the unit-test suite
kato sandbox-build       Build the hardened Docker sandbox image
kato sandbox-login       Interactive Claude login inside the sandbox
kato sandbox-verify      End-to-end smoke test of the sandbox
```

## Adding a new target

Edit the `_TARGETS` dict in [`kato.py`](./kato.py). On Windows, rebuild
the binary:

```powershell
.\.venv\Scripts\python.exe tools\kato\build.py
```

POSIX hosts pick up the change automatically (the bash wrapper just
invokes `kato.py` directly).

The macOS/Linux side keeps the existing [`Makefile`](../../Makefile) for
operators with muscle memory; both files reference the same underlying
scripts so the two codepaths stay in sync as long as the script names
don't change.
