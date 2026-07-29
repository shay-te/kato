//! Run the Kato backend inside a WSL2 distribution, driven from the native
//! Windows shell — the VS Code Remote-WSL model.
//!
//! WHY: on Windows, the Docker sandbox (`KATO_CLAUDE_DOCKER=true`) is refused
//! outright — the sandbox image is Linux, the workspace path validation assumes
//! POSIX semantics, and `fcntl.flock` for the audit chain doesn't exist. Inside
//! WSL2 all of it works, because WSL2 *is* Linux. So the good Windows setup is:
//! native window here, backend over there.
//!
//! THE HARD REQUIREMENT: the operator must never install or update Kato inside
//! the distro by hand. So the Windows installer carries the Linux backend
//! binary, and this module pushes it into the distro keyed by version —
//! `~/.kato/bin/kato-sidecar-<version>` — exactly how VS Code manages
//! `~/.vscode-server`. Shell updates itself → next launch pushes the matching
//! backend → no skew, no `pip install`, no `git pull`.
//!
//! WHAT IT STILL CANNOT DO: `git`, the agent CLI and Docker live in the distro
//! and are the operator's install. Kato drives them as subprocesses; they are
//! the product, not a dependency we can bundle (VS Code doesn't install your
//! compilers either). `preflight_missing_tools` reports them instead of
//! stalling on the splash.
//!
//! PLATFORM NOTE: everything here is compiled on every platform on purpose —
//! only the *decision* to use WSL is `#[cfg(windows)]` (see `resolve_target`).
//! Keeping the logic cfg-free means `cargo check` on a Mac still type-checks
//! the Windows path, which is the only compile-time signal available when CI
//! is Linux-only.

#[cfg(any(windows, test))]
use std::path::PathBuf;
use std::process::Command;

use serde::{Deserialize, Serialize};

/// Name of the Linux backend binary shipped inside the Windows installer.
/// Matches the PyInstaller output that `sidecar/build-sidecar.sh` produces on
/// a Linux runner, and the `resources` entry in `tauri.windows.conf.json`.
pub const LINUX_SIDECAR_RESOURCE: &str = "binaries/kato-sidecar-x86_64-unknown-linux-gnu";

/// Where the backend runs for this launch.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendTarget {
    /// Spawn the bundled sidecar for THIS OS (today's behavior everywhere).
    Native,
    /// Spawn the pushed Linux sidecar inside `distro` via `wsl.exe`.
    Wsl { distro: String },
}

/// One row of `wsl.exe -l -v`.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WslDistro {
    pub name: String,
    pub version: u8,
    pub is_default: bool,
}

/// Persisted choice. Lives on the WINDOWS side (`%USERPROFILE%\.kato\`), not in
/// `settings.json`: the target has to be known BEFORE any backend exists, and
/// each side has its own `~/.kato`. Same reason the login-shell PATH cache is a
/// separate file.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SavedTarget {
    pub target: String,
    #[serde(default)]
    pub distro: String,
}

#[cfg(any(windows, test))]
const TARGET_FILE: &str = "desktop-target.json";

/// Hide the console window `wsl.exe` would otherwise flash on every probe —
/// this is a GUI subsystem app (`windows_subsystem = "windows"`), so spawning
/// a console program pops a black box unless CREATE_NO_WINDOW is set.
#[cfg(windows)]
fn hide_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_command: &mut Command) {}

/// Decode console output that may be UTF-16LE.
///
/// `wsl.exe` writes UTF-16LE by default — parsing it as UTF-8 yields a string
/// with a NUL between every character and silently matches nothing, which is a
/// deeply confusing way to see "no distros found". Modern builds honor
/// `WSL_UTF8=1` (set on every call below); this handles the ones that don't.
pub fn decode_console_output(bytes: &[u8]) -> String {
    let body = if bytes.starts_with(&[0xFF, 0xFE]) { &bytes[2..] } else { bytes };
    let looks_utf16 = body.len() >= 2
        && body.len() % 2 == 0
        && body.iter().skip(1).step_by(2).take(64).all(|b| *b == 0);
    if looks_utf16 {
        let units: Vec<u16> = body
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        String::from_utf16_lossy(&units)
    } else {
        String::from_utf8_lossy(body).into_owned()
    }
}

/// Result of one `wsl.exe` invocation. `None` from the runners below means
/// wsl.exe could not be executed at all (WSL not installed).
pub struct WslOutput {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

impl WslOutput {
    /// stderr when it says something, else stdout — the shape an error
    /// message wants.
    pub fn message(&self) -> String {
        let err = self.stderr.trim();
        if err.is_empty() { self.stdout.trim().to_string() } else { err.to_string() }
    }
}

/// Run `wsl.exe` with `args` and capture its (possibly UTF-16) output.
pub fn run_wsl(args: &[&str]) -> Option<WslOutput> {
    let mut command = Command::new("wsl.exe");
    command.args(args).env("WSL_UTF8", "1");
    hide_console(&mut command);
    let output = command.output().ok()?;
    Some(WslOutput {
        ok: output.status.success(),
        stdout: decode_console_output(&output.stdout),
        stderr: decode_console_output(&output.stderr),
    })
}

/// Parse the table `wsl.exe -l -v` prints.
///
/// Distro names CAN contain spaces ("Ubuntu 22.04 LTS" from the Store), so the
/// name is everything before the last two columns — not `split_whitespace()[0]`.
#[cfg(any(windows, test))]
pub fn parse_distro_list(text: &str) -> Vec<WslDistro> {
    let mut out = Vec::new();
    for line in text.lines() {
        let raw = line.trim_end();
        let trimmed = raw.trim();
        if trimmed.is_empty() || trimmed.starts_with("NAME") {
            continue;
        }
        let is_default = trimmed.starts_with('*');
        let rest = trimmed.trim_start_matches('*').trim();
        let mut parts: Vec<&str> = rest.split_whitespace().collect();
        let version = match parts.pop().and_then(|v| v.parse::<u8>().ok()) {
            Some(value) => value,
            None => continue,
        };
        if parts.pop().is_none() {
            continue; // no STATE column — not a distro row
        }
        let name = parts.join(" ");
        if name.is_empty() {
            continue;
        }
        out.push(WslDistro { name, version, is_default });
    }
    out
}

/// Every installed distribution, or an empty list when WSL is absent.
#[cfg(any(windows, test))]
pub fn list_distros() -> Vec<WslDistro> {
    match run_wsl(&["-l", "-v"]) {
        Some(output) if output.ok => parse_distro_list(&output.stdout),
        _ => Vec::new(),
    }
}

/// The distro to use when the operator hasn't chosen one: the default distro
/// if it is WSL2, else the first WSL2 one. WSL1 is skipped deliberately — it
/// has no real kernel, so Docker (the whole reason to prefer WSL) can't run.
#[cfg(any(windows, test))]
pub fn pick_distro(distros: &[WslDistro]) -> Option<String> {
    distros
        .iter()
        .find(|d| d.is_default && d.version >= 2)
        .or_else(|| distros.iter().find(|d| d.version >= 2))
        .map(|d| d.name.clone())
}

/// `<home>/.kato` on the side this shell runs on.
#[cfg(any(windows, test))]
pub fn windows_state_dir() -> Option<PathBuf> {
    let home = std::env::var("USERPROFILE")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var("HOME").ok())?;
    if home.is_empty() {
        return None;
    }
    Some(PathBuf::from(home).join(".kato"))
}

// The file/process helpers below only ever run on Windows, but they are
// compiled under `test` too so a `cargo check`/`cargo test` on macOS still
// type-checks them. CI is Linux-only and cross-checking the whole crate needs
// a Windows C toolchain (the updater pulls in `ring`), so this is the only
// compile-time signal this code gets before it reaches a Windows machine.
#[cfg(any(windows, test))]
pub fn read_saved_target() -> Option<BackendTarget> {
    let path = windows_state_dir()?.join(TARGET_FILE);
    let text = std::fs::read_to_string(path).ok()?;
    let saved: SavedTarget = serde_json::from_str(&text).ok()?;
    match saved.target.trim().to_ascii_lowercase().as_str() {
        "wsl" if !saved.distro.trim().is_empty() => {
            Some(BackendTarget::Wsl { distro: saved.distro })
        }
        "native" => Some(BackendTarget::Native),
        _ => None,
    }
}

#[cfg(any(windows, test))]
pub fn write_saved_target(target: &BackendTarget) {
    let Some(dir) = windows_state_dir() else { return };
    let saved = match target {
        BackendTarget::Native => SavedTarget { target: "native".into(), distro: String::new() },
        BackendTarget::Wsl { distro } => {
            SavedTarget { target: "wsl".into(), distro: distro.clone() }
        }
    };
    if let Ok(text) = serde_json::to_string_pretty(&saved) {
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::write(dir.join(TARGET_FILE), text);
    }
}

/// True when this machine already has a Windows-side Kato config.
///
/// Gates the default: an EXISTING native install must not be silently moved to
/// WSL by an update — its `settings.json`, tasks and workspaces all live on the
/// Windows side, and the distro has its own empty `~/.kato`. Moving them would
/// read as "the update deleted my configuration".
#[cfg(any(windows, test))]
pub fn has_windows_side_config() -> bool {
    windows_state_dir()
        .map(|dir| dir.join("settings.json").is_file())
        .unwrap_or(false)
}

/// Decide where the backend runs, and why — the whole defaulting policy, as a
/// pure function so it is testable off-Windows.
///
/// Returns `(target, note, persist)`. `note` is shown on the splash so the
/// operator always knows which side they are on (VS Code's `WSL: Ubuntu`
/// indicator, in the only place this shell has to say it). `persist` is false
/// when the choice merely echoes what was already saved.
#[cfg(any(windows, test))]
pub fn choose_target(
    saved: Option<BackendTarget>,
    has_windows_config: bool,
    distros: &[WslDistro],
) -> (BackendTarget, String, bool) {
    // An explicit remembered choice always wins — switching sides means
    // switching ~/.kato, so it is never re-decided behind the operator's back.
    if let Some(target) = saved {
        let note = match &target {
            BackendTarget::Wsl { distro } => format!("Backend: WSL ({distro})"),
            BackendTarget::Native => "Backend: Windows (native)".to_string(),
        };
        return (target, note, false);
    }
    // An EXISTING native install must not be moved to WSL by an update: its
    // settings.json, tasks and workspaces live on the Windows side, and the
    // distro has its own empty ~/.kato. Silently switching would read as
    // "the update deleted my configuration".
    if has_windows_config {
        return (
            BackendTarget::Native,
            "Backend: Windows (native) — existing configuration found".to_string(),
            true,
        );
    }
    match pick_distro(distros) {
        Some(distro) => {
            let note = format!("Backend: WSL ({distro}) — sandboxing available");
            (BackendTarget::Wsl { distro }, note, true)
        }
        None => {
            // No WSL, or WSL1 only. Never block: native works, it just cannot
            // run the Docker sandbox. Say why, once, and move on.
            let note = if distros.is_empty() {
                "Backend: Windows (native). Install WSL2 (`wsl --install`) to enable \
                 the sandboxed agent."
            } else {
                "Backend: Windows (native). Your WSL distributions are version 1, which \
                 cannot run the sandbox — `wsl --set-version <distro> 2` enables it."
            };
            (BackendTarget::Native, note.to_string(), true)
        }
    }
}

/// `choose_target` wired to the real machine. Only Windows can answer anything
/// but `Native`: elsewhere the bundled sidecar already IS the right OS.
pub fn resolve_target() -> (BackendTarget, String) {
    #[cfg(not(windows))]
    {
        (BackendTarget::Native, String::new())
    }
    #[cfg(windows)]
    {
        let (target, note, persist) =
            choose_target(read_saved_target(), has_windows_side_config(), &list_distros());
        if persist {
            write_saved_target(&target);
        }
        (target, note)
    }
}

/// Version-keyed name of the backend binary inside the distro. Bumping the app
/// version pushes a new one, so the shell and the backend can never disagree
/// (the failure mode of "just pip install it in the distro").
pub fn sidecar_binary_name() -> String {
    format!("kato-sidecar-{}", env!("CARGO_PKG_VERSION"))
}

/// The shell fragment that installs the backend into the distro.
///
/// Copies to `.tmp` then `mv`s, so a crash mid-copy can't leave a partial file
/// that the `-x` test would later accept as installed. Old versions are pruned
/// in the same pass. The Windows path arrives as `$1` (never interpolated into
/// the script) so spaces and backslashes in `C:\Program Files\...` can't break
/// the quoting.
const INSTALL_SCRIPT: &str = "set -e; \
     src=$(wslpath -u \"$1\"); \
     dir=\"$HOME/.kato/bin\"; \
     mkdir -p \"$dir\"; \
     if [ ! -x \"$dir/$2\" ]; then \
       cp \"$src\" \"$dir/$2.tmp\"; \
       chmod +x \"$dir/$2.tmp\"; \
       mv \"$dir/$2.tmp\" \"$dir/$2\"; \
     fi; \
     find \"$dir\" -maxdepth 1 -name 'kato-sidecar-*' ! -name \"$2\" -delete 2>/dev/null || true";

/// Push the bundled Linux backend into `distro` (no-op when already current).
pub fn install_sidecar(distro: &str, windows_source: &str, binary: &str) -> Result<(), String> {
    let output = run_wsl(&[
        "-d", distro, "--",
        "sh", "-c", INSTALL_SCRIPT, "_", windows_source, binary,
    ])
    .ok_or_else(|| "wsl.exe could not be started".to_string())?;
    if output.ok {
        Ok(())
    } else {
        Err(format!("could not install the Kato backend into {distro}: {}", output.message()))
    }
}

/// Which of `tools` are missing inside the distro.
///
/// One `wsl.exe` round trip for all of them, through a LOGIN shell (`-l`) so
/// tools installed via nvm / asdf / a profile export are visible — the same
/// reason the macOS path asks `$SHELL -ilc` for its PATH.
pub fn preflight_missing_tools(distro: &str, tools: &[&str]) -> Vec<String> {
    let script = "for tool in \"$@\"; do \
         command -v \"$tool\" >/dev/null 2>&1 || printf '%s\\n' \"$tool\"; \
       done";
    let mut args: Vec<&str> = vec!["-d", distro, "--", "bash", "-lc", script, "_"];
    args.extend_from_slice(tools);
    match run_wsl(&args) {
        Some(output) if output.ok => output
            .stdout
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect(),
        // Can't tell → don't invent a problem. A genuinely broken distro
        // surfaces through the spawn failing, with its own message.
        _ => Vec::new(),
    }
}

/// Kill a leftover backend inside the distro.
///
/// The Windows-side `wsl.exe` process is only a relay: killing IT does not kill
/// the Linux process holding port 5050, so without this the next launch
/// collides with a straggler — the white-screen bug, in its WSL form.
pub fn kill_stragglers(distro: &str) {
    let _ = run_wsl(&["-d", distro, "--", "pkill", "-9", "-f", "kato-sidecar"]);
}

/// Arguments for spawning the backend: a LOGIN shell inside the distro, so the
/// profile that puts `git` and the agent CLI on PATH is sourced before exec.
pub fn spawn_args(distro: &str, binary: &str) -> Vec<String> {
    vec![
        "-d".to_string(),
        distro.to_string(),
        "--".to_string(),
        "bash".to_string(),
        "-lc".to_string(),
        format!("exec \"$HOME/.kato/bin/{binary}\""),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_utf16_console_output() {
        // What `wsl.exe -l -v` actually emits without WSL_UTF8=1.
        let utf16: Vec<u8> = "Ubuntu\n".encode_utf16().flat_map(|u| u.to_le_bytes()).collect();
        assert_eq!(decode_console_output(&utf16), "Ubuntu\n");
    }

    #[test]
    fn decodes_utf16_with_a_bom() {
        let mut bytes = vec![0xFF, 0xFE];
        bytes.extend("Ubuntu".encode_utf16().flat_map(|u| u.to_le_bytes()));
        assert_eq!(decode_console_output(&bytes), "Ubuntu");
    }

    #[test]
    fn decodes_plain_utf8() {
        assert_eq!(decode_console_output(b"Ubuntu\n"), "Ubuntu\n");
        assert_eq!(decode_console_output(b""), "");
    }

    #[test]
    fn parses_the_distro_table() {
        let table = "  NAME            STATE           VERSION\n\
                     * Ubuntu          Running         2\n\
                       Debian          Stopped         1\n";
        let distros = parse_distro_list(table);
        assert_eq!(distros.len(), 2);
        assert_eq!(distros[0], WslDistro { name: "Ubuntu".into(), version: 2, is_default: true });
        assert_eq!(distros[1], WslDistro { name: "Debian".into(), version: 1, is_default: false });
    }

    #[test]
    fn parses_distro_names_containing_spaces() {
        // Store-installed names really do look like this.
        let table = "  NAME                   STATE           VERSION\n\
                     * Ubuntu 22.04 LTS       Running         2\n";
        let distros = parse_distro_list(table);
        assert_eq!(distros[0].name, "Ubuntu 22.04 LTS");
        assert_eq!(distros[0].version, 2);
    }

    #[test]
    fn ignores_noise_and_malformed_rows() {
        let distros = parse_distro_list("\n  NAME  STATE  VERSION\nwsl: something went wrong\n");
        assert!(distros.is_empty());
    }

    #[test]
    fn prefers_the_default_wsl2_distro() {
        let distros = vec![
            WslDistro { name: "Alpine".into(), version: 2, is_default: false },
            WslDistro { name: "Ubuntu".into(), version: 2, is_default: true },
        ];
        assert_eq!(pick_distro(&distros), Some("Ubuntu".to_string()));
    }

    #[test]
    fn falls_back_to_any_wsl2_distro_when_the_default_is_wsl1() {
        let distros = vec![
            WslDistro { name: "Legacy".into(), version: 1, is_default: true },
            WslDistro { name: "Ubuntu".into(), version: 2, is_default: false },
        ];
        assert_eq!(pick_distro(&distros), Some("Ubuntu".to_string()));
    }

    #[test]
    fn wsl1_only_machines_get_no_distro() {
        // Docker can't run on WSL1, so there is no reason to prefer it.
        let distros = vec![WslDistro { name: "Legacy".into(), version: 1, is_default: true }];
        assert_eq!(pick_distro(&distros), None);
        assert_eq!(pick_distro(&[]), None);
    }

    #[test]
    fn spawn_uses_a_login_shell_and_the_versioned_binary() {
        let args = spawn_args("Ubuntu", "kato-sidecar-0.1.0");
        assert_eq!(args[0], "-d");
        assert_eq!(args[1], "Ubuntu");
        assert!(args.contains(&"-lc".to_string()));
        assert!(args.last().unwrap().contains("kato-sidecar-0.1.0"));
    }

    #[test]
    fn binary_name_is_version_keyed() {
        assert_eq!(sidecar_binary_name(), format!("kato-sidecar-{}", env!("CARGO_PKG_VERSION")));
    }

    #[test]
    fn install_script_never_interpolates_the_windows_path() {
        // Spaces/backslashes in "C:\Program Files\..." must arrive as $1.
        assert!(INSTALL_SCRIPT.contains("wslpath -u \"$1\""));
        assert!(!INSTALL_SCRIPT.contains("C:"));
        // Partial copies must not be mistaken for an installed backend.
        assert!(INSTALL_SCRIPT.contains(".tmp"));
        assert!(INSTALL_SCRIPT.contains("mv "));
    }

    #[test]
    fn saved_target_round_trips() {
        let json = r#"{"target":"wsl","distro":"Ubuntu"}"#;
        let saved: SavedTarget = serde_json::from_str(json).unwrap();
        assert_eq!(saved.target, "wsl");
        assert_eq!(saved.distro, "Ubuntu");
        // A distro-less record is tolerated (falls back to native).
        let bare: SavedTarget = serde_json::from_str(r#"{"target":"native"}"#).unwrap();
        assert_eq!(bare.distro, "");
    }

    #[test]
    fn non_windows_always_resolves_native() {
        #[cfg(not(windows))]
        assert_eq!(resolve_target().0, BackendTarget::Native);
    }

    // ---- the defaulting policy ----
    // This is the part with real consequences (a wrong default strands an
    // existing operator's config on the other side of the boundary), and the
    // part no CI runner can exercise. Hence pure-function + tests.

    fn ubuntu2() -> Vec<WslDistro> {
        vec![WslDistro { name: "Ubuntu".into(), version: 2, is_default: true }]
    }

    #[test]
    fn a_remembered_choice_always_wins_and_is_not_rewritten() {
        let saved = Some(BackendTarget::Wsl { distro: "Debian".into() });
        let (target, note, persist) = choose_target(saved, true, &ubuntu2());
        assert_eq!(target, BackendTarget::Wsl { distro: "Debian".into() });
        assert!(note.contains("Debian"));
        assert!(!persist, "echoing the saved choice must not rewrite the file");
    }

    #[test]
    fn a_remembered_native_choice_is_respected_even_with_wsl2_present() {
        let (target, _, _) = choose_target(Some(BackendTarget::Native), false, &ubuntu2());
        assert_eq!(target, BackendTarget::Native);
    }

    #[test]
    fn an_existing_windows_install_is_never_moved_to_wsl() {
        // The regression this guards: an update silently switching sides, so
        // settings.json / tasks / workspaces "disappear" into the other ~/.kato.
        let (target, note, persist) = choose_target(None, true, &ubuntu2());
        assert_eq!(target, BackendTarget::Native);
        assert!(note.contains("existing configuration"));
        assert!(persist);
    }

    #[test]
    fn a_fresh_install_with_wsl2_prefers_the_distro() {
        let (target, note, persist) = choose_target(None, false, &ubuntu2());
        assert_eq!(target, BackendTarget::Wsl { distro: "Ubuntu".into() });
        assert!(note.contains("Ubuntu"));
        assert!(persist);
    }

    #[test]
    fn no_wsl_falls_back_to_native_with_an_actionable_note() {
        let (target, note, _) = choose_target(None, false, &[]);
        assert_eq!(target, BackendTarget::Native);
        assert!(note.contains("wsl --install"));
    }

    #[test]
    fn wsl1_only_falls_back_to_native_and_says_how_to_upgrade() {
        let distros = vec![WslDistro { name: "Legacy".into(), version: 1, is_default: true }];
        let (target, note, _) = choose_target(None, false, &distros);
        assert_eq!(target, BackendTarget::Native);
        assert!(note.contains("set-version"));
    }

    #[test]
    fn every_outcome_tells_the_operator_which_side_they_are_on() {
        // The splash line is this shell's only "WSL: Ubuntu" indicator.
        for (saved, has_config, distros) in [
            (None, false, ubuntu2()),
            (None, true, ubuntu2()),
            (None, false, vec![]),
            (Some(BackendTarget::Native), false, vec![]),
        ] {
            let (_, note, _) = choose_target(saved, has_config, &distros);
            assert!(note.starts_with("Backend: "), "unhelpful note: {note}");
        }
    }
}
