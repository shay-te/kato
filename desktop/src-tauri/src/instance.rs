//! The kato already running on this machine's `~/.kato`, if any — and, on
//! Windows, the app's own leftover backends.
//!
//! Kato refuses a second kato on one `~/.kato` (its instance lock) and writes
//! who holds it to `~/.kato/kato.instance` (`{"pid", "port", …}`). The app
//! reads that file BEFORE starting its own kato, so a running one is named on
//! the splash at once. Its plain-HTTP health check could not see every kato:
//! `kato up` serves HTTPS by default, and a hung kato answers nothing — the
//! app then started a second kato on the next port, only for the lock to stop
//! it with a later, vaguer message.

use std::path::{Path, PathBuf};
use std::process::Command;

/// A kato holding `~/.kato`: its process, and the port it serves on.
#[derive(Debug, PartialEq, Eq)]
pub struct RunningKato {
    pub pid: u32,
    pub port: u16,
}

/// `kato.instance`, where kato writes it: beside the lock, which
/// `KATO_INSTANCE_LOCK_PATH` moves — as it does for kato.
pub fn instance_file(kato_dir: Option<PathBuf>) -> Option<PathBuf> {
    std::env::var("KATO_INSTANCE_LOCK_PATH")
        .ok()
        .filter(|path| !path.trim().is_empty())
        .map(PathBuf::from)
        .or_else(|| kato_dir.map(|dir| dir.join("kato.instance")))
}

/// The kato `kato.instance` names — while that process still runs and its
/// port is still taken. Both, not either: a kato that crashed leaves the file
/// behind, and its process id may since belong to something else.
pub fn running_kato(path: &Path, port_taken: impl Fn(u16) -> bool) -> Option<RunningKato> {
    let holder = parse_instance(&std::fs::read_to_string(path).ok()?)?;
    (port_taken(holder.port) && process_alive(holder.pid)).then_some(holder)
}

/// `kato.instance`'s text: `pid` a number, `port` a string (or a number).
fn parse_instance(text: &str) -> Option<RunningKato> {
    let value: serde_json::Value = serde_json::from_str(text).ok()?;
    let pid = value
        .get("pid")?
        .as_u64()
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid > 0)?;
    let port = crate::json_port(value.get("port")?)?;
    Some(RunningKato { pid, port })
}

#[cfg(not(windows))]
fn process_alive(pid: u32) -> bool {
    // `kill -0` sends nothing: it only asks whether the process exists.
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(windows)]
fn process_alive(pid: u32) -> bool {
    tasklist(&format!("PID eq {pid}"))
        .map(|listing| csv_lists_pid(&listing, pid))
        .unwrap_or(false)
}

/// `tasklist /FO CSV /NH` output with `pid` in a row's second column. A row
/// is `"image","pid","session","#","mem K"`; no match is one `INFO:` line.
#[cfg_attr(not(windows), allow(dead_code))]
fn csv_lists_pid(listing: &str, pid: u32) -> bool {
    let wanted = format!("\"{pid}\"");
    listing
        .lines()
        .any(|row| row.split(',').nth(1).is_some_and(|field| field.trim() == wanted))
}

#[cfg(windows)]
fn tasklist(filter: &str) -> Option<String> {
    let mut command = Command::new("tasklist");
    command.args(["/FI", filter, "/FO", "CSV", "/NH"]);
    crate::wsl::hide_console(&mut command);
    let output = command.output().ok()?;
    Some(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Every `kato-sidecar.exe` whose app is gone, with what it started:
/// stopped. True when there was one.
///
/// Windows does not end a process's children with it, so an app that was
/// force-closed — or hung, and was ended — leaves its backend running, holding
/// `~/.kato` and the port, and the next launch was refused. Only ORPHANS: there
/// is no single-instance guard, and a second window's backend belongs to an
/// app that is still running.
#[cfg(windows)]
pub fn kill_orphaned_sidecars() -> bool {
    // Nearly every launch has none: that costs one `tasklist`, not PowerShell.
    let listed = tasklist("IMAGENAME eq kato-sidecar.exe")
        .is_some_and(|listing| listing.to_ascii_lowercase().contains("\"kato-sidecar.exe\""));
    if !listed {
        return false;
    }
    let mut command = Command::new("powershell.exe");
    command.args([
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        STOP_ORPHANED_SIDECARS,
    ]);
    crate::wsl::hide_console(&mut command);
    command
        .output()
        .map(|output| !String::from_utf8_lossy(&output.stdout).trim().is_empty())
        .unwrap_or(false)
}

/// Prints the id of each orphaned sidecar it stopped (with its descendants).
///
/// Windows reuses process ids and never updates a child's parent id, so a
/// parent is only one that started BEFORE its child: a sidecar whose parent id
/// now belongs to a younger process is an orphan, and a process whose parent
/// id merely matches a sidecar's (its real parent long gone) is not that
/// sidecar's child — and is left alone.
///
/// Single-quoted throughout: the argument reaches PowerShell through the
/// Windows command line, where a double quote would need escaping.
#[cfg_attr(not(windows), allow(dead_code))]
const STOP_ORPHANED_SIDECARS: &str = "\
$all = @(Get-CimInstance Win32_Process); \
$byId = @{}; foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }; \
function Test-ChildOf($child, $parent) { \
$child.ParentProcessId -eq $parent.ProcessId -and $child.ProcessId -ne $parent.ProcessId \
-and $child.CreationDate -ge $parent.CreationDate }; \
function Stop-Tree($proc, [int]$depth) { \
if ($depth -gt 16) { return }; \
foreach ($c in $all) { if (Test-ChildOf $c $proc) { Stop-Tree $c ($depth + 1) } }; \
Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }; \
foreach ($p in $all) { \
if ($p.Name -eq 'kato-sidecar.exe') { \
$parent = $byId[[int]$p.ParentProcessId]; \
if (-not $parent -or -not (Test-ChildOf $p $parent)) { Stop-Tree $p 0; $p.ProcessId } } }";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_instance_file_names_the_process_and_its_port() {
        let text = r#"{"pid": 47012, "port": "5050", "started_at": 1760000000.5}"#;
        assert_eq!(parse_instance(text), Some(RunningKato { pid: 47012, port: 5050 }));
        // A hand edit may have put the port as a number.
        assert_eq!(
            parse_instance(r#"{"pid": 7, "port": 5051}"#),
            Some(RunningKato { pid: 7, port: 5051 })
        );
    }

    #[test]
    fn an_unreadable_instance_file_names_no_one() {
        for text in [
            "",
            "not json",
            "[]",
            r#"{"port": "5050"}"#,
            r#"{"pid": 0, "port": "5050"}"#,
            r#"{"pid": 12, "port": "web"}"#,
            r#"{"pid": 12, "port": "0"}"#,
            r#"{"pid": -3, "port": "5050"}"#,
        ] {
            assert_eq!(parse_instance(text), None, "{text}");
        }
    }

    #[test]
    fn the_lock_path_setting_moves_the_instance_file() {
        // Kept to one test: the variable is process-wide.
        std::env::remove_var("KATO_INSTANCE_LOCK_PATH");
        assert_eq!(
            instance_file(Some(PathBuf::from("/home/me/.kato"))),
            Some(PathBuf::from("/home/me/.kato/kato.instance"))
        );
        assert_eq!(instance_file(None), None);
        std::env::set_var("KATO_INSTANCE_LOCK_PATH", "/tmp/elsewhere/kato.instance");
        assert_eq!(
            instance_file(Some(PathBuf::from("/home/me/.kato"))),
            Some(PathBuf::from("/tmp/elsewhere/kato.instance"))
        );
        std::env::remove_var("KATO_INSTANCE_LOCK_PATH");
    }

    #[test]
    fn a_kato_counts_as_running_only_while_its_process_runs_and_holds_its_port() {
        let dir = std::env::temp_dir().join(format!("kato-instance-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("kato.instance");
        let me = std::process::id();
        std::fs::write(&path, format!(r#"{{"pid": {me}, "port": "5057"}}"#)).unwrap();
        assert_eq!(running_kato(&path, |port| port == 5057), Some(RunningKato { pid: me, port: 5057 }));
        // Its port is free again: it is gone, whatever runs under that id now.
        assert_eq!(running_kato(&path, |_| false), None);
        // The process is gone (a crashed kato leaves the file behind).
        std::fs::write(&path, r#"{"pid": 2000000000, "port": "5057"}"#).unwrap();
        assert_eq!(running_kato(&path, |_| true), None);
        // No file: no kato.
        std::fs::remove_file(&path).unwrap();
        assert_eq!(running_kato(&path, |_| true), None);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn tasklist_rows_are_matched_on_the_pid_column_only() {
        let listing = "\"python.exe\",\"47012\",\"Console\",\"1\",\"120,340 K\"\r\n";
        assert!(csv_lists_pid(listing, 47012));
        assert!(!csv_lists_pid(listing, 4701));
        // Its memory column holds digits too; only the pid column counts.
        assert!(!csv_lists_pid(listing, 340));
        assert!(!csv_lists_pid(
            "INFO: No tasks are running which match the specified criteria.\r\n",
            47012
        ));
    }

    #[test]
    fn the_orphan_script_never_needs_a_double_quote() {
        assert!(!STOP_ORPHANED_SIDECARS.contains('"'));
        assert!(STOP_ORPHANED_SIDECARS.contains("'kato-sidecar.exe'"));
    }
}
