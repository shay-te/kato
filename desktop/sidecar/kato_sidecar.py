"""Entry point for the frozen Kato sidecar embedded in the Tauri app.

`kato up` is a LAUNCHER, not the app: it runs `scripts/run_local.py`, which
loads `~/.kato/settings.json` into the environment and then execs
`python -m kato_core_lib.main` from the project venv. None of that re-exec
works in a frozen app — there's no venv, and `sys.executable` is THIS binary,
not python (that's the "invalid choice: .../run_local.py" error).

So we do what the launcher does, minus the re-exec:
  1. Load `~/.kato/settings.json` into the env (kato's precedence is
     shell > settings.json) — a stdlib-only mirror of run_local.py's reader,
     so the app honors the SAME config as your CLI (nothing lost).
  2. Run the real app entry — the `@hydra.main`-decorated
     `kato_core_lib.main.main` — DIRECTLY. Missing config isn't fatal: kato
     boots into SETUP MODE (the wizard) so a window still opens.
"""

import json
import os
import sys
import tempfile
from pathlib import Path


def _load_settings_into_env() -> None:
    """Fill env from `~/.kato/settings.json` (or `$KATO_SETTINGS_FILE`) for any
    key the real shell didn't already set — shell wins. Tolerant: a missing or
    corrupt file is a no-op (kato then boots into SETUP MODE). Mirrors
    scripts/_script_utils.read_kato_settings_file, which is itself a
    deliberate stdlib-only mirror — no kato import needed here.
    """
    path = os.environ.get("KATO_SETTINGS_FILE", "").strip() or str(
        Path.home() / ".kato" / "settings.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        os.environ.setdefault(str(key), str(value))


def _wire_ca_bundle() -> None:
    """Point OpenSSL + requests at certifi's bundled CA bundle.

    A FROZEN app has no system CA store wired into its OpenSSL: the compiled-in
    cert paths point at the BUILD machine and don't resolve at runtime, so TLS
    verification fails with "unable to get local issuer certificate" — kato's
    boot pins api.anthropic.com's cert via ``ssl.create_default_context()``,
    which reads ``SSL_CERT_FILE`` from the env. Frozen-only so a dev interpreter
    (with a real system store, possibly a corporate MITM CA) is left untouched;
    ``setdefault`` so an explicit shell override still wins.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import certifi
    except Exception:
        return
    ca = certifi.where()
    if os.path.exists(ca):
        os.environ.setdefault("SSL_CERT_FILE", ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)


def _wire_desktop_defaults() -> None:
    """Desktop-app defaults the frozen sidecar sets before kato boots.

    All via ``setdefault`` (AFTER settings.json is loaded) so an explicit shell
    / settings.json value still wins:

    * ``KATO_WEBSERVER_HTTPS=0`` — serve the planning UI over plain HTTP on
      loopback. kato defaults to HTTPS with a self-signed cert it installs into
      the OS trust store; in the desktop webview that trust-install can fail on
      a fresh machine (cert not yet trusted → the window shows a cert error and
      never loads). The server binds 127.0.0.1 only, so HTTP costs no real
      security here, and the Tauri shell navigates to http://127.0.0.1:<port>.
    * ``KATO_OPEN_BROWSER=0`` — the desktop app IS the UI (its own native
      window), so kato must NOT also pop open a browser tab on boot (it opens
      one by default).
    """
    os.environ.setdefault("KATO_WEBSERVER_HTTPS", "0")
    os.environ.setdefault("KATO_OPEN_BROWSER", "0")


def _prepare_working_dir() -> list[str]:
    """Fix a Dock/Finder launch running with CWD='/' (read-only).

    hydra's ``@hydra.main`` creates ``outputs/<date>/<time>/`` RELATIVE to the
    current directory, so a GUI launch (whose CWD is '/') crashes the boot with
    ``OSError: [Errno 30] Read-only file system: 'outputs'`` — the "hangs on the
    splash" bug. Move to a writable dir (~/.kato, kato's own state dir) so any
    CWD-relative code is safe, and redirect hydra's run dir to a temp path +
    skip its ``.hydra`` config dump so no ``outputs/`` clutter is created.
    Returns the hydra ``sys.argv`` overrides.
    """
    home = Path.home()
    for candidate in (home / ".kato", home, Path(tempfile.gettempdir())):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            os.chdir(candidate)
            break
        except Exception:
            continue
    run_dir = Path(tempfile.gettempdir()) / "kato-desktop-hydra"
    return [f"hydra.run.dir={run_dir}", "hydra.output_subdir=null"]


def _run() -> int:
    _load_settings_into_env()
    _wire_desktop_defaults()
    _wire_ca_bundle()
    hydra_overrides = _prepare_working_dir()
    # `@hydra.main` parses sys.argv for config overrides. Pass ONLY our hydra
    # run-dir overrides (a GUI launch's CWD='/' is read-only, so hydra's default
    # ``outputs/`` dir can't be created) — nothing else the shell handed us.
    sys.argv = [sys.argv[0], *hydra_overrides]

    from kato_core_lib import main as kato_main

    result = kato_main.main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(_run())
