"""Put saved connection settings to work, and check them again — ONE way.

The connection settings (the ticket system, the git hosts, the repositories
folder) are read when kato starts: the clients are built from them then. So a
fix saved in Settings did nothing until a restart, while "Test connection" —
which checks the FORM — said "Connected" and the banner kept saying kato could
not reach Jira (2026-11-15: "make sure when the test succeeds the banner is
checked as well — all the same code connected").

Every caller goes through ``recheck``: Settings after a successful test or a
save, and the startup loop that keeps retrying a start that could not connect.

* The saved connection settings differ from the ones kato started with →
  kato restarts itself to use them (``restarting``) — unless an agent is
  working, which a restart would cut off (``restart_needed``, naming them).
* Otherwise kato runs its own connection check again now (``checked``). That
  check reports into the one connection status (``connection_health``) the
  banner reads — so the banner follows what kato itself can reach.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

# Settings the clients are built from at start: changing one needs a restart.
CONNECTION_SETTING_PREFIXES = (
    'YOUTRACK_', 'JIRA_', 'GITHUB_', 'GITLAB_', 'BITBUCKET_', 'AZURE_',
)
CONNECTION_SETTING_KEYS = ('KATO_ISSUE_PLATFORM', 'REPOSITORY_ROOT_PATH')

RESTARTING = 'restarting'
RESTART_NEEDED = 'restart_needed'
CHECKED = 'checked'


def connection_settings_fingerprint(settings: Mapping[str, str]) -> str:
    """A digest of the saved connection settings (empty values are unset)."""
    picked = sorted(
        (str(key), str(value)) for key, value in (settings or {}).items()
        if str(value or '').strip()
        and (str(key) in CONNECTION_SETTING_KEYS or str(key).startswith(CONNECTION_SETTING_PREFIXES))
    )
    return hashlib.sha256(repr(picked).encode('utf-8')).hexdigest()


class ConnectionRecheck(object):
    def __init__(
        self,
        *,
        check: Callable[[], None],
        saved_settings: Callable[[], Mapping[str, str]],
        working_tasks: Callable[[], list[str]],
        restart: Callable[[], None],
    ) -> None:
        self._check = check
        self._saved_settings = saved_settings
        self._working_tasks = working_tasks
        self._restart = restart
        # What kato started with: the clients in this process were built from it.
        self._started_with = self._fingerprint()

    def settings_changed(self) -> bool:
        """The saved connection settings are no longer the ones kato started with."""
        return self._fingerprint() != self._started_with

    def recheck(self) -> dict:
        """``{status: restarting | restart_needed | checked, ok, error, working}``."""
        if self.settings_changed():
            working = list(self._working_tasks())
            if working:
                return {'status': RESTART_NEEDED, 'ok': False, 'working': working,
                        'error': 'restart kato to use the new settings once these finish: ' + ', '.join(working)}
            self._restart()
            return {'status': RESTARTING, 'ok': True, 'working': [], 'error': ''}
        try:
            self._check()
        except Exception as exc:  # noqa: BLE001 - the check's failure is the answer
            return {'status': CHECKED, 'ok': False, 'working': [], 'error': str(exc) or exc.__class__.__name__}
        return {'status': CHECKED, 'ok': True, 'working': [], 'error': ''}

    def _fingerprint(self) -> str:
        try:
            return connection_settings_fingerprint(self._saved_settings())
        except Exception:  # noqa: BLE001 - an unreadable file is "unchanged", never a restart
            return getattr(self, '_started_with', '')
