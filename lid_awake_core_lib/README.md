# lid_awake_core_lib

Keep a computer running with its lid closed, and never leave it stuck that way.

A computer that really sleeps runs nothing, so the only way to keep working is
to stop it sleeping. Every operating system does that differently, and the hold
that survives a closed lid is the dangerous one: left on, the computer stays on
in a bag until it is flat or hot. So the lib is built around that.

| | Lid hold | Idle hold | Crash guard | Heat | Setup |
|---|---|---|---|---|---|
| **Mac** | `pmset disablesleep` | `caffeinate -w` | root LaunchDaemon | `thermalState`, no root | sudo, once |
| **Windows** | `powercfg` lid action | `SetThreadExecutionState` | hidden PowerShell + logon Run value | none | no elevation, once |
| **Linux** | `systemd-inhibit` | `systemd-inhibit` | built into the inhibited command | `/sys/class/thermal` | none |

WSL is unsupported because Windows owns the lid there, and so is Linux with no
logind. On Linux the lib can prove logind holds the inhibitor, not that the
desktop obeys it: KDE's PowerDevil handles the lid itself.

| Module | What it does |
|---|---|
| `decision.py` | The rules. No I/O. Anything unknown means "let it sleep". |
| `service.py` | The tick that composes everything through one `PlatformSupport`. |
| `platform_support.py` | What each platform has to supply. |
| `platforms.py` | Picks this computer's parts. The only place that asks which OS it is. |
| `heartbeat.py` | The host's proof that it is still alive. |
| `commands.py` | Runs one command, bounded, with no terminal or console window. |
| `mac_*`, `idle_sleep_blocker.py` | The Mac's parts. |
| `windows_*` | The Windows parts. |
| `linux_*` | The Linux parts. |

## The rules

| Situation | Computer |
|---|---|
| Turned off, unsupported, or no working guard | Sleeps normally |
| Battery, heat or charger could not be read | Sleeps normally |
| Heat level serious or critical | Sleeps normally |
| On the charger | Stays awake |
| On battery, 20% or below | Sleeps normally |
| On battery, no heat sensor at all | Sleeps normally |
| On battery, an agent working | Stays awake |
| On battery, nothing working | Sleeps normally |

## Crash safety

The host writes a heartbeat file every tick while it holds the computer awake.
The guard watches it:

- **Fresh:** the host is alive, nothing to do.
- **Stale for 2 minutes:** the host crashed or hung, so it restores normal sleep.
- **No file:** the host holds nothing, so an operator's own setting is left alone.

The service refuses to hold anything unless `guard_ready()` confirms the guard.

## Using it

```python
host = HostIdentity(
    who='example',
    why='agents are working',
    heartbeat=Path.home() / '.example' / 'lid-awake.heartbeat',
    mac=GuardIdentity(
        label='com.example.lid-awake-watchdog',
        directory='/Library/Application Support/Example',
        sudoers_name='example-lid-awake',
    ),
    windows=WindowsGuardIdentity(
        run_value='ExampleLidAwakeWatchdog',
        script=Path.home() / '.example' / 'lid-awake-watchdog.ps1',
        saved=Path.home() / '.example' / 'lid-awake-lid-action.json',
    ),
)
support = platform_support(host)
support.install_guard()  # once, in a terminal; a Mac asks for the password

service = LidAwakeService(
    is_enabled=lambda: read_my_setting(),
    agents_working=lambda: any_agent_is_busy(),
    heartbeat=Heartbeat(host.heartbeat),
    support=support,
)
service.start()
...
service.stop()  # restores sleep on a clean exit
```
