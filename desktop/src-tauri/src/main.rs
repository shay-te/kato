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

use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const HOST: &str = "127.0.0.1";
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

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(move |app| {
            let app_handle = app.handle().clone();

            // Kill any LEFTOVER kato-sidecar (from a prior crash / force-quit
            // that skipped the exit handler) BEFORE spawning ours, so this
            // launch's sidecar owns the port and serves THIS bundle — not a
            // stale process's old one. Runs before our own spawn, so it only
            // hits stragglers. Best-effort; a no-op on Windows.
            #[cfg(not(windows))]
            {
                let _ = std::process::Command::new("/usr/bin/pkill")
                    .args(["-9", "-f", "kato-sidecar"])
                    .status();
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
            let (mut rx, child) = app
                .shell()
                .sidecar("kato-sidecar")
                .expect("kato-sidecar was not bundled (run `npm run sidecar`)")
                .env("PATH", augmented_path())
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
                    let url = format!("http://{HOST}:{port}/?_={nonce}");
                    if let Some(win) = app_handle.get_webview_window("main") {
                        let _ = win.navigate(url.parse().expect("invalid url"));
                    }
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
                if let Ok(mut guard) = sidecar.lock() {
                    if let Some(child) = guard.take() {
                        let _ = child.kill();
                    }
                }
            }
        });
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
