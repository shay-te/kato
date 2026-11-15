"""HTTP routes for quick start — a first run that asks only what kato cannot find.

    GET    /api/setup/quick-start              everything the screen needs: each chat agent (Claude
                                               Code, Codex, Grok, OpenCode), git, the code folders
                                               found, the folder offered when none
    GET    /api/setup/quick-start/checks       ?agent=: that agent + git only (polled while the
                                               operator installs or signs in)
    POST   /api/setup/quick-start              {agent, folder, create}: save it (400 = why not)
    POST   /api/setup/claude-sign-in           run Claude Code's sign-in
    GET    /api/setup/claude-sign-in           where it is
    POST   /api/setup/claude-sign-in/code      {code}: the code the sign-in page showed
    POST   /api/setup/claude-sign-in/cancel
    POST   /api/setup/install-git              Mac only: open Apple's command line tools installer

Installing an agent is the in-app upgrade's own job (``POST /api/agent-version/upgrade``
with its ``backend``): one installer, one progress bar.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.quick_start.agent_check import CLAUDE, agent_status, all_agents_status
from kato_core_lib.data_layers.service.quick_start.claude_sign_in import ClaudeSignIn, SignInError
from kato_core_lib.data_layers.service.quick_start.code_folders import find_code_folders, new_folder
from kato_core_lib.data_layers.service.quick_start.git_check import git_status, start_mac_install
from kato_core_lib.data_layers.service.quick_start.settings import QuickStartError, save_quick_start
from kato_core_lib.helpers.chat_agent_utils import chat_agent, normalized_chat_agent
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.kato_settings_store_utils import effective_config_env, write_kato_settings


def register_quick_start_routes(app: Flask, *, sign_in: ClaudeSignIn | None = None) -> None:
    signing_in = sign_in or ClaudeSignIn()

    @app.get('/api/setup/quick-start')
    def quick_start():
        env = effective_config_env()
        home = Path.home()
        workspaces = kato_home_path('workspaces', env_key='KATO_WORKSPACES_ROOT')
        return jsonify({
            'agents': all_agents_status(env),
            # Nothing is picked for the operator: '' until they chose one before.
            'agent': chat_agent(env),
            'git': git_status(),
            'folders': [folder.to_dict() for folder in find_code_folders(home, exclude=[workspaces])],
            'new_folder': str(new_folder(home)),
            'home': str(home),
            'folder': str(env.get('REPOSITORY_ROOT_PATH') or ''),
        })

    @app.get('/api/setup/quick-start/checks')
    def quick_start_checks():
        agent = normalized_chat_agent(request.args.get('agent')) or CLAUDE
        return jsonify({'agent': agent_status(agent, effective_config_env()), 'git': git_status()})

    @app.post('/api/setup/quick-start')
    def save():
        body = request.get_json(silent=True) or {}
        try:
            saved = save_quick_start(
                str(body.get('folder') or ''), agent=str(body.get('agent') or ''),
                create=body.get('create') is True, env=effective_config_env(), write=write_kato_settings,
            )
        except QuickStartError as exc:
            return jsonify({'error': str(exc)}), 400
        except OSError as exc:
            return jsonify({'error': f'failed to write settings file: {exc}'}), 500
        return jsonify({'ok': True, 'saved': saved})

    @app.post('/api/setup/claude-sign-in')
    def start_sign_in():
        return jsonify(signing_in.start(agent_status(CLAUDE, effective_config_env())['binary']))

    @app.get('/api/setup/claude-sign-in')
    def sign_in_status():
        return jsonify(signing_in.status())

    @app.post('/api/setup/claude-sign-in/code')
    def sign_in_code():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(signing_in.submit_code(str(body.get('code') or '')))
        except SignInError as exc:
            return jsonify({'error': str(exc)}), 400

    @app.post('/api/setup/claude-sign-in/cancel')
    def cancel_sign_in():
        return jsonify(signing_in.cancel())

    @app.post('/api/setup/install-git')
    def install_git():
        if not start_mac_install():
            return jsonify({'error': 'kato can open the installer only on a Mac'}), 400
        return jsonify({'ok': True})
