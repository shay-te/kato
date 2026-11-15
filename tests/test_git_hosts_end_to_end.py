"""Every git host, for real: what Settings saves → how kato boots → pull requests.

Why this file exists (2026-11-15). GitHub was set to "use my git login". Test
connection said Connected; every pull request failed "missing pull request API
token". Thousands of tests passed, because each one stubbed the step that broke:
the boot exported settings.json as it was and never turned the login into a
token. The operator: "so much bugs and you have million of tests — make sure
other git providers will not create more issues".

So nothing in the chain is stubbed here:

* the settings file is what the Settings form saves (cleared fields as "");
* the env is built by the REAL launchers — ``kato up`` (scripts/_script_utils)
  and the desktop app's sidecar (desktop/sidecar/kato_sidecar.py);
* a child process runs main's first boot step, composes kato's REAL config,
  walks REAL git repositories (one per host, remotes as people have them) and
  sets each one's pull request API up exactly as before opening a PR;
* "use my git login" goes through REAL ``git credential fill`` (a ``store``
  helper in a throwaway HOME; the system's helper switched off so no real
  login is ever read).

And for every host, Test connection must see exactly what the boot sees.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / 'scripts'))
from _script_utils import layered_env, read_kato_settings_file  # noqa: E402

_CONFIG_DIR = _ROOT / 'kato_core_lib' / 'config'

# One repository per host, remotes the way people have them.
REMOTES = {
    'gh-app': 'https://github.com/acme/gh-app.git',
    'gh-ssh': 'git@github.com:acme/gh-ssh.git',
    'gl-app': 'https://gitlab.com/acme/gl-app.git',
    'bb-app': 'https://bitbucket.org/acme/bb-app.git',
    'az-app': 'https://dev.azure.com/acme/shop/_git/az-app',
}
HOST_OF = {'gh-app': 'github', 'gh-ssh': 'github', 'gl-app': 'gitlab', 'bb-app': 'bitbucket', 'az-app': 'azure'}
LABEL = {'github': 'GitHub', 'gitlab': 'GitLab', 'bitbucket': 'Bitbucket', 'azure': 'Azure DevOps'}
API = {
    'github': 'https://api.github.com',
    'gitlab': 'https://gitlab.com/api/v4',
    'bitbucket': 'https://api.bitbucket.org/2.0',
    'azure': 'https://dev.azure.com',
}

# Every connection field of the Git provider tab, as the form saves an untouched
# one: "" (webserver/kato_webserver/app.py _GIT_HOST_FIELDS).
FORM_FIELDS = (
    'GITHUB_API_BASE_URL', 'GITHUB_API_TOKEN', 'GITHUB_API_TOKEN_SOURCE', 'GITHUB_OWNER', 'GITHUB_REPO',
    'GITLAB_API_BASE_URL', 'GITLAB_API_TOKEN', 'GITLAB_API_TOKEN_SOURCE', 'GITLAB_PROJECT',
    'BITBUCKET_API_BASE_URL', 'BITBUCKET_API_TOKEN', 'BITBUCKET_API_TOKEN_SOURCE', 'BITBUCKET_USERNAME',
    'BITBUCKET_API_EMAIL', 'BITBUCKET_WORKSPACE', 'BITBUCKET_REPO_SLUG',
    'AZURE_API_BASE_URL', 'AZURE_API_TOKEN', 'AZURE_USERNAME',
)

# How each way of signing in is saved, and the token each host must end up with.
SCENARIOS = {
    'pasted': {
        'settings': {
            'GITHUB_API_TOKEN': 'gh-pasted', 'GITLAB_API_TOKEN': 'gl-pasted',
            'BITBUCKET_API_TOKEN': 'bb-pasted', 'BITBUCKET_API_EMAIL': 'me@example.com',
            'AZURE_API_TOKEN': 'az-pasted',
        },
        'tokens': {'github': 'gh-pasted', 'gitlab': 'gl-pasted', 'bitbucket': 'bb-pasted', 'azure': 'az-pasted'},
    },
    'git login': {
        'settings': {
            'GITHUB_API_TOKEN_SOURCE': 'git-credential', 'GITLAB_API_TOKEN_SOURCE': 'git-credential',
            'BITBUCKET_API_TOKEN_SOURCE': 'git-credential', 'BITBUCKET_API_EMAIL': 'me@example.com',
            'AZURE_API_TOKEN': 'az-pasted',
        },
        'git_logins': {
            'github.com': ('me', 'gh-from-git'),
            'gitlab.com': ('me', 'gl-from-git'),
            'bitbucket.org': ('me', 'bb-from-git'),
        },
        'tokens': {'github': 'gh-from-git', 'gitlab': 'gl-from-git', 'bitbucket': 'bb-from-git', 'azure': 'az-pasted'},
    },
    'environment': {
        'settings': {
            'GITHUB_API_TOKEN_SOURCE': 'environment', 'GITLAB_API_TOKEN_SOURCE': 'environment',
            'BITBUCKET_API_TOKEN_SOURCE': 'environment', 'BITBUCKET_API_EMAIL': 'me@example.com',
            'AZURE_API_TOKEN': 'az-pasted',
        },
        'shell': {'GH_TOKEN': 'gh-env', 'GITLAB_TOKEN': 'gl-env', 'BITBUCKET_TOKEN': 'bb-env'},
        'tokens': {'github': 'gh-env', 'gitlab': 'gl-env', 'bitbucket': 'bb-env', 'azure': 'az-pasted'},
    },
    # The 2026-11-15 Bitbucket case, for every host: the terminal kato starts
    # from carries OLD tokens (a stale .env the editor loads), Settings hold
    # the new ones. Settings must win — "no .env, only kato settings".
    'stale terminal': {
        'settings': {
            'GITHUB_API_TOKEN_SOURCE': 'git-credential', 'GITLAB_API_TOKEN': 'gl-new',
            'BITBUCKET_API_TOKEN': 'bb-new', 'BITBUCKET_API_EMAIL': 'me@example.com',
            'AZURE_API_TOKEN': 'az-new',
        },
        'git_logins': {'github.com': ('me', 'gh-from-git')},
        'shell': {
            'GITHUB_API_TOKEN': 'gh-old', 'GITLAB_API_TOKEN': 'gl-old', 'BITBUCKET_API_TOKEN': 'bb-old',
            'BITBUCKET_API_EMAIL': 'old@example.com', 'AZURE_API_TOKEN': 'az-old',
        },
        'tokens': {'github': 'gh-from-git', 'gitlab': 'gl-new', 'bitbucket': 'bb-new', 'azure': 'az-new'},
    },
    'nothing set': {
        # Bitbucket has a token but no email: the second thing it needs.
        'settings': {'BITBUCKET_API_TOKEN': 'bb-pasted'},
        'errors': {
            'github': 'add a GitHub token in Settings → Git provider → GitHub',
            'gitlab': 'add a GitLab token in Settings → Git provider → GitLab',
            'bitbucket': 'add your Atlassian account email in Settings → Git provider → Bitbucket',
            'azure': 'add an Azure DevOps token in Settings → Git provider → Azure DevOps',
        },
    },
}

# The child: main()'s first boot step, then kato's real config and repository
# service, then — per repository — what runs before a pull request is opened.
_CHILD = r'''
import json, os, sys
from kato_core_lib.helpers.kato_settings_store_utils import (
    config_env_with_form, load_kato_settings_into_environ,
)
mode, config_dir = sys.argv[1], sys.argv[2]
load_kato_settings_into_environ()                 # main(): first thing
if mode == 'test':                                # Test connection's child env
    env = config_env_with_form({})
    os.environ.clear()
    os.environ.update(env)
from hydra import compose, initialize_config_dir
from kato_core_lib.data_layers.service.repository_service import RepositoryService
out = {}
with initialize_config_dir(config_dir=config_dir, version_base=None):
    service = RepositoryService(compose(config_name='kato_core_lib').kato, 1)
    for repository in service._ensure_repositories():
        try:
            service._prepare_pull_request_api(repository)
            service._pull_request_data_access(repository)   # builds the host's client
            out[repository.id] = {
                'provider': repository.provider, 'token': repository.token,
                'base_url': repository.provider_base_url,
            }
        except Exception as exc:
            out[repository.id] = {'error': str(exc)}
print(json.dumps(out))
'''


def _git(*args: str) -> None:
    subprocess.run(['git', *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _sidecar_loader():
    spec = importlib.util.spec_from_file_location('kato_sidecar_under_test', _ROOT / 'desktop' / 'sidecar' / 'kato_sidecar.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._load_settings_into_env


class _Scenario(object):
    """A throwaway HOME with settings.json, repositories and git logins."""

    def __init__(self, base: Path, name: str, spec: dict) -> None:
        self.home = base / name.replace(' ', '-')
        (self.home / '.kato').mkdir(parents=True)
        root = self.home / 'code'
        for repo, remote in REMOTES.items():
            (root / repo).mkdir(parents=True)
            _git('init', '-q', str(root / repo))
            _git('-C', str(root / repo), 'remote', 'add', 'origin', remote)
        settings = {key: '' for key in FORM_FIELDS}
        settings.update(spec['settings'])
        settings.update({
            'REPOSITORY_ROOT_PATH': str(root), 'KATO_ISSUE_PLATFORM': 'local',
            'KATO_AGENT_BACKEND': 'claude', 'KATO_WORKSPACES_ROOT': str(self.home / 'ws'),
        })
        (self.home / '.kato' / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
        logins = spec.get('git_logins') or {}
        if logins:
            (self.home / '.gitconfig').write_text('[credential]\n\thelper = store\n', encoding='utf-8')
            (self.home / '.git-credentials').write_text(
                ''.join(f'https://{user}:{token}@{host}\n' for host, (user, token) in logins.items()),
                encoding='utf-8',
            )
        self.shell = {
            'PATH': os.environ.get('PATH', ''), 'HOME': str(self.home), 'USERPROFILE': str(self.home),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            # Never the computer's own credential helper (a real Keychain login).
            'GIT_CONFIG_NOSYSTEM': '1',
            **(spec.get('shell') or {}),
        }

    def launcher_env(self) -> dict:
        with patch.dict(os.environ, self.shell, clear=True):
            return layered_env(self.shell, read_kato_settings_file())

    def desktop_env(self) -> dict:
        load = _sidecar_loader()
        with patch.dict(os.environ, self.shell, clear=True):
            load()
            return dict(os.environ)

    def env_for(self, mode: str) -> dict:
        """The env kato starts with. Reads the PROCESS env (patched): call it
        from one thread only — two at once restore each other's patch and
        leave a scenario's HOME behind for every test after."""
        return {**(self.desktop_env() if mode == 'desktop' else self.launcher_env()), 'PYTHONPATH': str(_ROOT)}

    @staticmethod
    def run(mode: str, env: dict) -> dict:
        """Thread-safe: touches nothing but its own child process."""
        done = subprocess.run(
            [sys.executable, '-c', _CHILD, 'test' if mode == 'test' else 'boot', str(_CONFIG_DIR)],
            env=env, cwd=str(_ROOT), capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
        )
        for line in reversed(done.stdout.splitlines()):
            if line.startswith('{'):
                return json.loads(line)
        raise AssertionError(f'{mode}: the child gave no answer\n{done.stderr[-3000:]}')


MODES = ('launcher', 'desktop', 'test')


class GitHostsEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        scenarios = {name: _Scenario(base, name, spec) for name, spec in SCENARIOS.items()}
        jobs = [(name, mode) for name in scenarios for mode in MODES]
        envs = [scenarios[name].env_for(mode) for name, mode in jobs]  # one thread
        with ThreadPoolExecutor(max_workers=6) as pool:
            answers = list(pool.map(lambda job_env: _Scenario.run(job_env[0][1], job_env[1]), zip(jobs, envs)))
        cls.answers = dict(zip(jobs, answers))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _each(self, scenario: str):
        for mode in MODES:
            answer = self.answers[(scenario, mode)]
            self.assertEqual(set(answer), set(REMOTES), f'{scenario}/{mode}: repositories found: {answer}')
            for repo in REMOTES:
                with self.subTest(scenario=scenario, mode=mode, repository=repo):
                    yield mode, repo, HOST_OF[repo], answer[repo]

    def _signs_in(self, scenario: str) -> None:
        tokens = SCENARIOS[scenario]['tokens']
        for mode, repo, host, got in self._each(scenario):
            self.assertNotIn('error', got, f'{mode} {repo}: {got.get("error")}')
            self.assertEqual(got['provider'], host)
            self.assertEqual(got['token'], tokens[host])
            self.assertEqual(got['base_url'], API[host])

    def test_a_pasted_token_reaches_every_host(self) -> None:
        self._signs_in('pasted')

    def test_use_my_git_login_reaches_every_host(self) -> None:
        # The KAN-1 case, for every host that offers it.
        self._signs_in('git login')

    def test_a_token_from_the_environment_reaches_every_host(self) -> None:
        self._signs_in('environment')

    def test_settings_win_over_old_tokens_in_the_terminal_for_every_host(self) -> None:
        self._signs_in('stale terminal')

    def test_nothing_set_says_where_in_settings_for_every_host(self) -> None:
        errors = SCENARIOS['nothing set']['errors']
        for mode, repo, host, got in self._each('nothing set'):
            self.assertIn(errors[host], got.get('error', ''), f'{mode} {repo}')
            self.assertNotIn('_API_', got.get('error', ''), 'an env var name is no advice in the app')

    def test_test_connection_sees_exactly_what_kato_runs_with(self) -> None:
        # The bug was a "Connected" on one side and "missing token" on the other.
        for scenario in SCENARIOS:
            runs = {mode: self.answers[(scenario, mode)] for mode in MODES}
            with self.subTest(scenario=scenario):
                self.assertEqual(runs['test'], runs['launcher'])
                self.assertEqual(runs['desktop'], runs['launcher'])


class LaunchersAgreeTests(unittest.TestCase):
    """``kato up``, the desktop app and kato's own boot step apply ONE rule
    (``settings_first_env``) — the launchers keep stdlib copies of it, so
    each copy is held to the original here."""

    def test_every_launcher_applies_the_settings_first_rule(self) -> None:
        from kato_core_lib.helpers.kato_settings_store_utils import settings_first_env

        with tempfile.TemporaryDirectory() as tmp:
            scenario = _Scenario(Path(tmp), 'stale terminal', SCENARIOS['stale terminal'])
            launcher, desktop = scenario.launcher_env(), scenario.desktop_env()
            settings = json.loads((scenario.home / '.kato' / 'settings.json').read_text(encoding='utf-8'))
        rule = {key: value for key, value in settings_first_env(settings, scenario.shell).items()
                if not key.endswith('_API_TOKEN') or key in settings and settings[key]}
        self.assertEqual(launcher, desktop)
        for key, value in rule.items():
            if key == 'GITHUB_API_TOKEN':
                continue  # resolved from the git login by kato's boot step, not the launchers
            self.assertEqual(launcher.get(key), value, key)
        # The terminal's old values lose to Settings…
        self.assertEqual((launcher['BITBUCKET_API_TOKEN'], launcher['BITBUCKET_API_EMAIL']), ('bb-new', 'me@example.com'))
        # …and a cleared field is never exported as "" (it would kill its default).
        for key in FORM_FIELDS:
            if not settings.get(key) and not scenario.shell.get(key):
                self.assertNotIn(key, launcher, f'{key}="" would kill its default')

if __name__ == '__main__':
    unittest.main()
