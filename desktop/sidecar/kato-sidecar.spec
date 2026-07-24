# PyInstaller spec for the Kato sidecar (frozen Python backend embedded in the
# Tauri app). Build via ./build-sidecar.sh — do NOT run kato from here at runtime.
#
# Tuned for the LIGHTEST bundle:
#   * strips the ~40 MB of UI source maps (never shipped),
#   * excludes the optional security scanners by default (they degrade
#     gracefully — see INCLUDE_SECURITY_SCANNERS below),
#   * strips debug symbols + optimizes bytecode,
#   * excludes GUI/test/notebook stacks kato never uses.
#
# The one thing we must NOT trim: Kato's LAZY client factories (see CLAUDE.md) —
# agent transports, PR providers, task providers are imported dynamically inside
# factory functions, so PyInstaller misses them. We collect them explicitly.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# SPECPATH = this file's dir (desktop/sidecar); repo root is two levels up.
REPO = Path(SPECPATH).resolve().parent.parent

# --- Size toggle: the optional security scanners add ~20-40 MB and each runner
#     degrades to a warning if its tool is missing (repo pyproject.toml notes
#     this), so they're EXCLUDED by default. Flip to True to ship in-app scans.
INCLUDE_SECURITY_SCANNERS = False
_SCANNERS = ["bandit", "safety", "detect_secrets"]

# --- Hidden imports: the lazily-loaded provider/transport libs + dynamic frameworks.
_LAZY_LIBS = [
    "claude_core_lib", "codex_core_lib", "openhands_core_lib",         # agent transports
    "github_core_lib", "gitlab_core_lib", "bitbucket_core_lib",        # PR providers
    "youtrack_core_lib", "jira_core_lib",                             # task providers
    "agent_backend_core_lib", "agent_core_lib", "sandbox_core_lib",   # factories + bases
    "provider_client_base", "repository_core_lib", "task_core_lib",
    "git_core_lib", "workspace_core_lib", "kato_core_lib",
    # The Flask planning webserver — imported lazily inside a try/except in
    # kato_core_lib/main.py, so static analysis misses it. Without it kato boots
    # headless ("planning webserver not available") and NO window ever opens.
    "kato_webserver",
    # Instantiated ONLY via a hydra `_target_` string (the email client), so
    # PyInstaller's static analysis can't see it:
    "email_core_lib", "core_lib",
]
hiddenimports = []
for _lib in _LAZY_LIBS:
    hiddenimports += collect_submodules(_lib)
hiddenimports += collect_submodules("hydra")      # hydra resolves config groups dynamically
hiddenimports += collect_submodules("omegaconf")
# Kato's hydra SEARCH-PATH plugins (hydra_plugins/<lib>/…) add each provider's
# config package to the search path. Hydra discovers them by scanning the
# `hydra_plugins` namespace — bundle them all, or config composition fails with
# "Could not find '<lib>/<lib>'".
hiddenimports += collect_submodules("hydra_plugins")
if INCLUDE_SECURITY_SCANNERS:
    for _lib in _SCANNERS:
        hiddenimports += collect_submodules(_lib)


# --- Data files: bundle the UI + templates, but SKIP source maps (~40 MB).
def _tree(src_root: Path, dest_prefix: str, exclude_suffixes=()):
    out = []
    if not src_root.exists():
        return out
    for f in src_root.rglob("*"):
        if not f.is_file():
            continue
        if any(f.name.endswith(s) for s in exclude_suffixes):
            continue
        dest_dir = str(Path(dest_prefix) / f.relative_to(src_root).parent)
        out.append((str(f), dest_dir))
    return out


datas = []
# certifi's CA bundle — a frozen app has no system CA store wired into OpenSSL,
# so TLS verification (kato pins api.anthropic.com's cert at boot via
# ssl.create_default_context) fails without it. kato_sidecar.py points
# SSL_CERT_FILE at this bundled cacert.pem.
datas += collect_data_files("certifi")
# The Flask webserver resolves static/templates from its REPO_ROOT, which in the
# FROZEN app is sys._MEIPASS (see kato_webserver/app.py's frozen branch), so
# these must land at the BUNDLE ROOT — not under webserver/. kato.png (the
# logo/favicon route reads KATO_REPO_ROOT/kato.png) sits at that root too.
datas += _tree(REPO / "webserver/static", "static", exclude_suffixes=(".map",))
datas += _tree(REPO / "webserver/templates", "templates")
_kato_png = REPO / "kato.png"
if _kato_png.exists():
    datas.append((str(_kato_png), "."))
# Non-Python RUNTIME data the libs ship. collect_data_files() misses these (the
# libs resolve as non-packages under pathex), so we walk the known dirs. Paths
# mirror the source layout so hydra's `config_path` + template loaders resolve.
datas += _tree(REPO / "kato_core_lib/config", "kato_core_lib/config")          # hydra config (main.py @hydra.main config_path='config') — REQUIRED
datas += _tree(REPO / "kato_core_lib/templates", "kato_core_lib/templates")    # jinja templates
# Each provider lib ships a hydra config package that its search-path plugin
# adds as pkg://<lib>.<lib>.config — bundle each at that exact import path.
for _plib in ("youtrack_core_lib", "github_core_lib", "bitbucket_core_lib",
              "gitlab_core_lib", "jira_core_lib"):
    datas += _tree(REPO / _plib / _plib / "config", f"{_plib}/{_plib}/config")
for _asset in ("Dockerfile", "init-firewall.sh", "entrypoint.sh"):            # sandbox image assets
    _p = REPO / "sandbox_core_lib/sandbox_core_lib" / _asset
    if _p.exists():
        datas.append((str(_p), "sandbox_core_lib/sandbox_core_lib"))
datas += _tree(REPO / "tools", "tools")        # kato shells to these
datas += _tree(REPO / "scripts", "scripts")

# --- Excludes: never-used stacks (+ the scanners when off).
#
# STARTUP COST: the frozen app's launch time is dominated by dlopen-ing +
# code-signature-validating every bundled C-extension dylib (and, for the
# one-file build, re-extracting them all each launch). PyInstaller's static
# analysis greedily pulls in heavy transitive packages that kato NEVER imports
# — django (+ its 14 MB admin static tree and a runtime hook that does
# ``import django.utils.autoreload`` on EVERY startup), numpy, shapely, PIL,
# psycopg2, pandas. Excluding them (verified unused: kato is a Flask app, grep
# finds zero imports) trims ~60 MB and a big chunk of the pre-main startup time.
# Any of these is still ImportError-safe to drop: kato's own code never imports
# them, and the only consumers are optional/try-except paths in third-party deps
# kato doesn't exercise. If a future dep genuinely needs one, remove it here.
_excludes = ["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
             "pytest", "IPython", "notebook", "jupyter", "sphinx",
             "django", "numpy", "shapely", "PIL", "Pillow", "psycopg2",
             "psycopg2-binary", "pandas"]
if not INCLUDE_SECURITY_SCANNERS:
    _excludes += _SCANNERS

a = Analysis(
    ["kato_sidecar.py"],
    # REPO resolves the FLAT core-libs (REPO/<lib>/__init__.py). kato_webserver
    # lives one level deeper (REPO/webserver/kato_webserver), so its source root
    # must be on pathex too — otherwise PyInstaller's static graph can't follow
    # the lazy `import kato_webserver` in main.py (it doesn't run the PEP-660
    # editable meta-finder, only plain sys.path/pathex resolution).
    pathex=[str(REPO), str(REPO / "webserver")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=2,        # -OO: drop asserts + docstrings (smaller)
)
pyz = PYZ(a.pure)

# ONE-FILE build: Tauri's ``externalBin`` sidecar must be a single executable, so
# everything goes into one EXE. For faster startup, a one-dir COLLECT build
# shipped via Tauri ``resources`` + a launcher is the alternative.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="kato-sidecar",
    console=True,       # kato logs to stdout; the Tauri shell pipes it
    debug=False,
    bootloader_ignore_signals=False,
    # strip=False on purpose: PyInstaller's strip step corrupts some macOS
    # dylibs (e.g. _decimal → libmpdec), and the follow-up install_name_tool
    # rpath rewrite then fails with "malformed object". Size cost is small.
    strip=False,
    upx=False,          # UPX breaks macOS codesigning/notarization — leave off
    runtime_tmpdir=None,
)
