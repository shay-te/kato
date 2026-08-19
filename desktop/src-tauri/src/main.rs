// Kato desktop shell (Tauri v2).
//
// Startup sequence:
//   1. Spawn the bundled `kato-sidecar` (frozen Python) as `kato up` — this boots
//      Kato's Flask planning webserver + scan loop, exactly like the CLI.
//   2. Pipe the sidecar's stdout/stderr to this process's console.
//   3. Wait until the webserver accepts TCP connections.
//   4. Navigate the (already-visible) splash window to the local UI.
// Built-in signed auto-update is wired via the updater plugin (see
// tauri.conf.json → plugins.updater).
//
// NOTE (can't be compiled in the authoring env): this targets Tauri 2.x. The
// two spots most likely to need a minor API tweak on your first `cargo build`
// are the sidecar spawn/`CommandEvent` match and `WebviewWindow::navigate`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod wsl;

use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

use wsl::BackendTarget;

// Bind/probe address: numeric, because that is what the sidecar binds and
// what a TCP readiness probe needs.
const HOST: &str = "127.0.0.1";
// Address the WEBVIEW is pointed at. Deliberately different from HOST.
//
// Two reasons it must be the name, not the number. On Windows the webview
// renders a blank page for the numeric loopback address but loads fine for
// `localhost` — the operator-visible symptom that started this. And browser
// certificate exceptions are per ORIGIN, so `https://127.0.0.1` and
// `https://localhost` are separate trust decisions; pinning the UI to one
// name keeps kato's local CA doing its job instead of the operator clicking
// through a warning.
const UI_HOST: &str = "localhost";
const DEFAULT_PORT: u16 = 5050;
// First boot can take ~a minute — the frozen sidecar validates the agent CLI +
// provider connections before the webserver comes up — so allow generous slack.
const READY_TIMEOUT: Duration = Duration::from_secs(180);

fn main() {
    // Shared handle to the sidecar child so the exit handler can kill it.
    // Otherwise the frozen kato process keeps running on port 5050 and the NEXT
    // launch connects to that STALE sidecar (serving the OLD UI bundle) instead
    // of spawning a fresh one — the "rebuilt app still shows the old UI" bug.
    let sidecar: std::sync::Arc<
        std::sync::Mutex<Option<tauri_plugin_shell::process::CommandChild>>,
    > = std::sync::Arc::new(std::sync::Mutex::new(None));
    let sidecar_setup = sidecar.clone();
    // Which side the backend ended up on — the exit handler needs it too,
    // because a WSL backend has to be killed from inside the distro.
    let target_state: std::sync::Arc<std::sync::Mutex<BackendTarget>> =
        std::sync::Arc::new(std::sync::Mutex::new(BackendTarget::Native));
    let target_setup = target_state.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Lets the web UI open external links (Claude output, docs, PR URLs)
        // in the system default browser instead of trying to navigate the
        // app's own webview. Driven from the frontend via utils/tauriLinks.js.
        .plugin(tauri_plugin_opener::init())
        // Native OS notifications. The UI calls this through
        // utils/tauriNotifications.js; the web Notification API it uses in a
        // browser is inert inside the desktop webview (see Cargo.toml).
        .plugin(tauri_plugin_notification::init())
        .setup(move |app| {
            let app_handle = app.handle().clone();

            // WHERE does the backend run? Native everywhere except Windows,
            // which prefers a WSL2 distro because that is the only place the
            // Docker sandbox works. Decided (and remembered) before anything
            // is spawned — see wsl.rs for the defaulting rules.
            let (target, target_note) = wsl::resolve_target();
            if !target_note.is_empty() {
                println!("[kato] {target_note}");
                let _ = app.handle().emit("kato-log", target_note.clone());
            }
            if let Ok(mut guard) = target_setup.lock() {
                *guard = target.clone();
            }

            // Kill any LEFTOVER kato-sidecar (from a prior crash / force-quit
            // that skipped the exit handler) BEFORE spawning ours, so this
            // launch's sidecar owns the port and serves THIS bundle — not a
            // stale process's old one. Runs before our own spawn, so it only
            // hits stragglers. Best-effort.
            match &target {
                // The Windows-side wsl.exe is only a relay — killing it does
                // NOT kill the Linux process on the port, so the straggler has
                // to be cleared from INSIDE the distro.
                BackendTarget::Wsl { distro } => wsl::kill_stragglers(distro),
                BackendTarget::Native => {
                    #[cfg(not(windows))]
                    {
                        let _ = std::process::Command::new("/usr/bin/pkill")
                            .args(["-9", "-f", "kato-sidecar"])
                            .status();
                    }
                }
            }

            // Clearing a straggler is async — the OS takes a beat to release the
            // socket. WAIT for the port to be genuinely free before spawning the
            // new sidecar; otherwise the new one races the dying one for the
            // bind, fails, and the window lands on a half-dead server (the
            // blank-white-on-2nd-launch bug). Bounded so a genuinely-stuck port
            // can't hang boot forever.
            if let Ok(addr) = format!("{HOST}:{DEFAULT_PORT}").parse::<SocketAddr>() {
                let mut waited_ms = 0u64;
                while waited_ms < 4000
                    && TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok()
                {
                    std::thread::sleep(Duration::from_millis(150));
                    waited_ms += 150;
                }
            }

            // 1) Spawn the frozen kato sidecar with no args — its entry
            // (kato_sidecar.py) loads ~/.kato/settings.json into the env and
            // runs kato's real app (kato_core_lib.main) DIRECTLY, bypassing the
            // `kato up` launcher (which re-execs a venv python that doesn't
            // exist when frozen).
            //
            // DATA CONTINUITY: we do NOT set KATO_HOME or override HOME, so the
            // frozen kato resolves the SAME ~/.kato (settings.json, tasks,
            // workspaces) and ~/.claude (sessions) as your CLI — nothing is
            // "lost"; the desktop app and `kato up` share one state dir.
            //
            // PATH: a Finder/Dock-launched app inherits a minimal PATH (it does
            // NOT load your shell profile), so it would fail to find git /
            // docker / the agent CLI. We prepend the usual tool locations.
            //
            // WSL TARGET: instead of the host sidecar we push the bundled
            // LINUX binary into the distro (version-keyed, like
            // ~/.vscode-server) and exec it through wsl.exe. The operator
            // never installs or updates kato inside the distro by hand — the
            // shell's own signed update carries the matching backend.
            let command = match &target {
                BackendTarget::Native => app
                    .shell()
                    .sidecar("kato-sidecar")
                    .expect("kato-sidecar was not bundled (run `npm run sidecar`)")
                    // A GUI launch has no shell profile, so PATH is rebuilt for
                    // the host case. NOT applied to wsl.exe: WSL derives the
                    // distro's PATH itself, and forcing Windows paths onto it
                    // would corrupt the Linux environment (the login shell in
                    // spawn_args does that job instead).
                    .env("PATH", augmented_path()),
                BackendTarget::Wsl { distro } => {
                    let binary = wsl::sidecar_binary_name();
                    match prepare_wsl_backend(app.handle(), distro, &binary) {
                        Ok(()) => app
                            .shell()
                            .command("wsl.exe")
                            .args(wsl::spawn_args(distro, &binary)),
                        Err(message) => {
                            // Fail LOUDLY on the splash rather than spinning:
                            // a missing tool inside the distro is a fixable
                            // setup problem, and silence is what made the
                            // original "stuck on the splash" bug so opaque.
                            let fatal = format!("KATO-FATAL: {message}");
                            eprintln!("[kato] {fatal}");
                            let _ = app.handle().emit("kato-log", fatal);
                            return Ok(());
                        }
                    }
                }
            };
            let (mut rx, child) = command
                .spawn()
                .expect("failed to spawn kato sidecar");
            // Hold the child so the RunEvent::Exit handler can kill it on quit.
            if let Ok(mut guard) = sidecar_setup.lock() {
                *guard = Some(child);
            }

            // 2) Pipe sidecar output to this console AND onto the splash window,
            // so a slow first boot (agent-CLI validation can take ~a minute)
            // reads as live progress instead of a frozen spinner.
            let log_handle = app.handle().clone();
            // Tee the sidecar output to ~/.kato/kato-desktop.log so a FINDER
            // launch failure (whose stdout goes to /dev/null) is diagnosable —
            // truncated each launch so it holds only the latest boot.
            let mut boot_log = std::env::var("HOME").ok().and_then(|home| {
                std::fs::OpenOptions::new()
                    .create(true)
                    .write(true)
                    .truncate(true)
                    .open(format!("{home}/.kato/kato-desktop.log"))
                    .ok()
            });
            tauri::async_runtime::spawn(async move {
                use std::io::Write;
                while let Some(event) = rx.recv().await {
                    let line = match event {
                        CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                            String::from_utf8_lossy(&bytes).into_owned()
                        }
                        // The sidecar process exited before it ever served the UI.
                        // Emit a fatal marker so the splash shows an error instead
                        // of spinning "loading" forever.
                        CommandEvent::Terminated(payload) => {
                            format!(
                                "KATO-FATAL: the Kato service exited (code {:?}) before it \
                                 could start. Open ~/.kato/kato-desktop.log for details.",
                                payload.code
                            )
                        }
                        CommandEvent::Error(err) => {
                            format!("KATO-FATAL: could not run the Kato service: {err}")
                        }
                        _ => continue,
                    };
                    print!("[kato] {line}");
                    if let Some(file) = boot_log.as_mut() {
                        let _ = file.write_all(line.as_bytes());
                        let _ = file.flush();
                    }
                    let trimmed = line.trim();
                    if !trimmed.is_empty() {
                        // Best-effort: the splash listens for "kato-log" and shows
                        // the latest line. Ignored once we navigate to the UI.
                        let _ = log_handle.emit("kato-log", trimmed.to_string());
                    }
                }
            });

            // 3 + 4) Wait for the server (blocking poll off the main thread),
            // then point the splash window at the local UI.
            std::thread::spawn(move || {
                let port = std::env::var("KATO_WEBSERVER_PORT")
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(DEFAULT_PORT);
                if wait_for_port(HOST, port, READY_TIMEOUT) {
                    // Loopback HTTP. If you run Kato's webserver with its
                    // self-signed HTTPS, either disable TLS for the desktop
                    // loopback case or add cert-pinning here (see README).
                    //
                    // Cache-bust the index: WKWebView keeps a PERSISTENT cache
                    // keyed on the app identifier, so after an app update it can
                    // serve the STALE index (→ old app.js/app.css) and the new
                    // UI never appears. A per-launch nonce forces a fresh index
                    // fetch; kato's own ?v=<mtime> on the bundles does the rest.
                    let nonce = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_millis())
                        .unwrap_or(0);
                    let url = format!("http://{UI_HOST}:{port}/?_={nonce}");
                    // Open a FRESH window pointed straight at the local server,
                    // then close the splash — instead of navigate()-ing the
                    // existing splash webview to the external URL. Navigating an
                    // already-shown (and, on a relaunch, macOS-restored) webview
                    // to an external site is exactly the path that comes up
                    // blank white on the 2nd and later launches: it works the
                    // very first time, then not. A brand-new window that loads
                    // the URL from creation paints reliably every launch — the
                    // normal way a Tauri app loads its content. No resize /
                    // hide-show / sleep hacks. Window creation must run on the
                    // main thread.
                    let ui_handle = app_handle.clone();
                    let _ = app_handle.run_on_main_thread(move || {
                        let built = tauri::WebviewWindowBuilder::new(
                            &ui_handle,
                            "app",
                            tauri::WebviewUrl::External(
                                url.parse().expect("invalid url"),
                            ),
                        )
                        .title("Kato")
                        .inner_size(1280.0, 860.0)
                        .min_inner_size(900.0, 600.0)
                        .focused(true)
                        // Match the splash background (#0f1115) so there's no
                        // white flash between the splash closing and the UI
                        // painting — the native window bg shows during that gap.
                        .background_color(tauri::window::Color(15, 17, 21, 255))
                        .build();
                        match built {
                            Ok(_) => {
                                if let Some(splash) =
                                    ui_handle.get_webview_window("main")
                                {
                                    let _ = splash.close();
                                }
                            }
                            Err(e) => {
                                eprintln!(
                                    "[kato] failed to open app window: {e}"
                                );
                                // Fallback to the old navigate path so the app
                                // isn't left with no content at all.
                                if let Some(splash) =
                                    ui_handle.get_webview_window("main")
                                {
                                    let _ = splash.navigate(
                                        url.parse().expect("invalid url"),
                                    );
                                }
                            }
                        }
                    });
                } else {
                    eprintln!("[kato] webserver did not become ready within timeout");
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Kato desktop")
        .run(move |_app_handle, event| {
            // Kill the sidecar when the app exits so it never lingers on the
            // port for the next launch to collide with (which would make the
            // next launch serve this stale process's OLD bundle).
            if let tauri::RunEvent::Exit = event {
                // A WSL backend outlives the wsl.exe relay we spawned, so it
                // has to be killed inside the distro or the next launch
                // collides with it on port 5050.
                if let Ok(guard) = target_state.lock() {
                    if let BackendTarget::Wsl { distro } = &*guard {
                        wsl::kill_stragglers(distro);
                    }
                }
                if let Ok(mut guard) = sidecar.lock() {
                    if let Some(child) = guard.take() {
                        // PyInstaller onefile forks a Python child that actually
                        // binds the port; killing only the parent leaks it (the
                        // zombie the next launch then collides with → white
                        // screen). Kill the child's children first, then the
                        // parent, so the port is fully released on quit.
                        #[cfg(not(windows))]
                        {
                            let _ = std::process::Command::new("/usr/bin/pkill")
                                .args(["-9", "-P", &child.pid().to_string()])
                                .status();
                        }
                        let _ = child.kill();
                    }
                }
            }
        });
}

/// Make `distro` ready to run the backend: push the bundled Linux binary in,
/// then check the tools kato shells out to are present.
///
/// The push is the whole point of the WSL target — the operator must never
/// `pip install` or `git pull` inside the distro. The Windows installer carries
/// the Linux binary; this copies it to `~/.kato/bin/kato-sidecar-<version>` and
/// skips the copy when that version is already there, so it costs nothing after
/// the first launch on a given version.
///
/// Returns a message fit to show the operator, because every failure here is
/// something they can fix: a missing resource means a broken install, a missing
/// `git` means one apt-get away.
fn prepare_wsl_backend(
    app: &tauri::AppHandle,
    distro: &str,
    binary: &str,
) -> Result<(), String> {
    let source = app
        .path()
        .resolve(wsl::LINUX_SIDECAR_RESOURCE, tauri::path::BaseDirectory::Resource)
        .map_err(|e| format!("the Linux backend is missing from this install ({e})"))?;
    let source_text = source
        .to_str()
        .ok_or_else(|| "the Linux backend path is not valid text".to_string())?;
    wsl::install_sidecar(distro, source_text, binary)?;

    // git + the agent CLI are the operator's install inside the distro — kato
    // drives them as subprocesses and cannot bundle them (VS Code doesn't ship
    // your compilers either). Name them now instead of stalling later.
    let missing = wsl::preflight_missing_tools(distro, &["git", "claude"]);
    if missing.is_empty() {
        return Ok(());
    }
    Err(format!(
        "{} is not installed inside {distro}. Kato runs its agent there, so install it \
         in that distribution (a Windows-side install is a different machine as far as \
         kato is concerned), then reopen Kato.",
        missing.join(" and "),
    ))
}

/// Ask the user's LOGIN shell for its real PATH.
///
/// A Finder/Dock launch does NOT source your shell profile, so tools installed
/// via nvm / fnm / asdf / Homebrew (git, node, docker, the `claude` CLI) are
/// absent from the minimal PATH the app inherits — kato then can't find them
/// and its boot stalls at agent-CLI validation (the "stuck on the splash" bug).
/// Running `$SHELL -ilc` sources the login + interactive profiles, so the PATH
/// we get back is exactly what your terminal sees. The sentinel makes parsing
/// robust against any greeting the shell prints on stdout.
#[cfg(not(windows))]
fn login_shell_path() -> Option<String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let output = std::process::Command::new(&shell)
        .args(["-ilc", "printf '<<KPATH>>%s<<KPATH>>' \"$PATH\""])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let path = stdout.split("<<KPATH>>").nth(1)?.trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(path)
    }
}

/// Resolve the login-shell PATH, cached so we don't pay the shell-profile
/// sourcing cost (`$SHELL -ilc`, measured at ~0.8s) on EVERY launch — it sat
/// synchronously before the sidecar spawn, delaying the whole boot.
///
/// The cache lives at `~/.kato/.login-shell-path`. On a hit we return the
/// cached value immediately and refresh it in the background so a PATH change
/// (a newly installed tool) propagates on the NEXT launch. On a miss (first
/// ever launch) we compute it synchronously and persist it. Best-effort
/// throughout: any read/write failure just falls back to recomputing.
#[cfg(not(windows))]
fn cached_login_shell_path() -> Option<String> {
    let home = std::env::var("HOME").ok()?;
    let cache = std::path::PathBuf::from(format!("{home}/.kato/.login-shell-path"));
    if let Ok(contents) = std::fs::read_to_string(&cache) {
        let cached = contents.trim().to_string();
        if !cached.is_empty() {
            // Refresh for next launch off the hot path (best-effort).
            let cache_bg = cache.clone();
            std::thread::spawn(move || {
                if let Some(fresh) = login_shell_path() {
                    let _ = std::fs::write(&cache_bg, fresh);
                }
            });
            return Some(cached);
        }
    }
    // Cache miss (first launch / empty cache): compute now and persist.
    let resolved = login_shell_path()?;
    let _ = std::fs::create_dir_all(format!("{home}/.kato"));
    let _ = std::fs::write(&cache, &resolved);
    Some(resolved)
}

/// Build a PATH that a Finder/Dock-launched app can actually use to find the
/// host tools kato shells out to (git / docker / node / the agent CLI). A GUI
/// launch doesn't load your shell profile, so we FIRST ask the login shell for
/// its real PATH (nvm/fnm/asdf/homebrew), then add the usual fallback dirs.
fn augmented_path() -> String {
    let existing = std::env::var("PATH").unwrap_or_default();
    let home = std::env::var("HOME").unwrap_or_default();
    #[cfg(windows)]
    {
        // Windows GUI apps generally inherit the system PATH (git/docker live
        // there via their installers), so keep it as-is.
        let _ = home;
        existing
    }
    #[cfg(not(windows))]
    {
        let mut parts: Vec<String> = Vec::new();
        // The user's real login-shell PATH — where nvm/fnm/asdf/homebrew put
        // git, node, docker and the `claude` CLI. Without this a Dock launch
        // can't find those tools and kato's boot hangs. Cached so the ~0.8s
        // profile-sourcing cost is paid once, not every launch. Duplicates
        // below are harmless (the OS uses the first match).
        if let Some(login) = cached_login_shell_path() {
            parts.push(login);
        }
        for p in [
            "/opt/homebrew/bin", // Apple-silicon Homebrew
            "/usr/local/bin",    // Intel Homebrew / common installs
            "/usr/bin", "/bin", "/usr/sbin", "/sbin",
        ] {
            parts.push(p.to_string());
        }
        if !home.is_empty() {
            parts.push(format!("{home}/.local/bin"));
            parts.push(format!("{home}/bin"));
        }
        if !existing.is_empty() {
            parts.push(existing);
        }
        parts.join(":")
    }
}

/// Poll a TCP connect to `host:port` until it succeeds or `timeout` elapses.
fn wait_for_port(host: &str, port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .expect("invalid host:port");
    loop {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok() {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(400));
    }
}
