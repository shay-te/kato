"""Flask app entrypoint for the Kato planning UI.

Bridges browser tabs to live :class:`StreamingClaudeSession` instances
managed by the kato process. Uses Server-Sent Events (server→browser)
plus regular POST endpoints (browser→server) instead of WebSockets — same
functional surface, but reliable on Werkzeug's dev server.

Endpoints:
    GET  /                                              — HTML shell
    GET  /healthz                                       — liveness
    GET  /logo.png                                      — kato logo
    GET  /api/sessions                                  — list all session records
    GET  /api/sessions/<task_id>                        — one record + recent events
    GET  /api/sessions/<task_id>/events                 — SSE: live agent events
    GET  /api/sessions/<task_id>/files                  — repo file tree (Files tab)
    GET  /api/sessions/<task_id>/diff                   — committed + uncommitted diff
    GET  /api/sessions/<task_id>/commits?repo=<id>      — recent commits on a repo's task branch
    GET  /api/sessions/<task_id>/commit?repo=<id>&sha=  — unified diff for one commit
    POST /api/sessions/<task_id>/messages               — body: {"text", "images": [{media_type, data}]}
    POST /api/sessions/<task_id>/permission             — body: {"request_id", "allow", "rationale"}
    POST /api/sessions/<task_id>/adopt-claude-session   — body: {"claude_session_id"}
    POST /api/sessions/<task_id>/sync-repositories      — clone task repos missing from workspace
    POST /api/sessions/<task_id>/add-repository         — body: {"repository_id"} — tag + clone
    GET  /api/repositories                              — list inventory repos for the chooser
    GET  /api/tasks                                     — every task assigned to kato (all states)
    POST /api/tasks/<task_id>/adopt                     — provision workspace + clones for a picked task
    GET  /api/sessions/<task_id>/comments?repo=<id>     — list local + synced-remote diff comments
    POST /api/sessions/<task_id>/comments               — add comment, immediately queue/run kato
    POST /api/sessions/<task_id>/comments/<id>/resolve  — mark thread resolved
    POST /api/sessions/<task_id>/comments/<id>/reopen   — re-open a resolved thread
    POST /api/sessions/<task_id>/comments/<id>/addressed — mark addressed + post on remote
    DEL  /api/sessions/<task_id>/comments/<id>          — delete comment + replies
    POST /api/sessions/<task_id>/comments/sync          — git pull + pull remote PR comments
    GET  /api/claude/sessions                           — list adoptable Claude Code sessions
    GET  /api/status/recent                             — recent kato-process log entries
    GET  /api/status/events                             — SSE: live kato-process log feed
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)

from claude_core_lib.claude_core_lib.session.wire_protocol import (
    CLAUDE_EVENT_CONTROL_REQUEST,
    CLAUDE_EVENT_PERMISSION_REQUEST,
    CLAUDE_EVENT_PERMISSION_RESPONSE,
    CLAUDE_EVENT_RESULT,
    SSE_EVENT_SESSION_CLOSED,
    SSE_EVENT_SESSION_EVENT,
    SSE_EVENT_SESSION_HISTORY_EVENT,
    SSE_EVENT_SESSION_IDLE,
    SSE_EVENT_SESSION_MISSING,
    SSE_EVENT_STATUS_DISABLED,
    SSE_EVENT_STATUS_ENTRY,
)
from kato_webserver.git_diff_utils import (
    conflicted_paths,
    current_branch,
    detect_default_branch,
    diff_against_base,
    diff_for_commit,
    ensure_branch_checked_out,
    list_branch_commits,
    tracked_file_tree,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
KATO_REPO_ROOT = REPO_ROOT.parent

# Browser-driven SSE stream cadence. The follow loop polls the
# session for new events and yields them as they arrive. We tried a
# Condition-based blocking wait once — it tested clean locally but
# stalled live-update delivery in production (events arrived only
# after a tab switch forced a fresh SSE connection). Until we can
# reproduce that reliably the safe primitive is a tight poll: 100ms
# of latency is invisible to humans, and the per-tick cost is now
# bounded by ``events_after`` (slice-read of only the new tail)
# rather than the old ``recent_events()`` full-list copy.
_SSE_POLL_INTERVAL_SECONDS = 0.1
# Periodic SSE comment that keeps proxies / load balancers from idling
# the connection out and lets the browser detect server crashes.
_SSE_HEARTBEAT_SECONDS = 15.0


def _record_cwd_or_none(manager, task_id: str) -> str | None:
    """Return the session's cwd if a record exists and points to a real dir."""
    record = manager.get_record(task_id)
    if record is None:
        return None
    cwd = getattr(record, 'cwd', '') or ''
    if not cwd or not Path(cwd).is_dir():
        return None
    return cwd


def _task_repository_ids(workspace_manager, task_id: str) -> list[str]:
    """Repository ids the workspace was provisioned with (in order).

    Falls back to a filesystem scan of ``<workspaces_root>/<task>/``
    when the workspace manager record is gone — which happens after
    the publish flow finishes and the in-memory record is cleared,
    even though the on-disk clones are still right there. Without
    this fallback the Files / Changes tabs would 404 immediately
    after pushing (the symptom Shubham hit), forcing the operator
    to reload to see the diff.
    """
    if workspace_manager is None:
        return []
    try:
        record = workspace_manager.get(task_id)
    except Exception:
        record = None
    if record is not None:
        repository_ids = getattr(record, 'repository_ids', []) or []
        if repository_ids:
            return [str(repo_id) for repo_id in repository_ids if repo_id]
    return _enumerate_repo_ids_from_disk(workspace_manager, task_id)


def _enumerate_repo_ids_from_disk(workspace_manager, task_id: str) -> list[str]:
    """List ``<repo>/.git`` directories under the task's workspace path.

    Used as the fallback for ``_task_repository_ids`` when the
    in-memory workspace record has been cleaned up but the clones
    are still on disk (post-publish, after a kato restart that lost
    its in-memory state, etc.).
    """
    if workspace_manager is None or not task_id:
        return []
    try:
        task_path = workspace_manager.workspace_path(task_id)
    except Exception:
        return []
    if not task_path.is_dir():
        return []
    discovered: list[str] = []
    try:
        entries = sorted(task_path.iterdir())
    except OSError:
        return []
    for repo_dir in entries:
        if not repo_dir.is_dir():
            continue
        if not (repo_dir / '.git').exists():
            continue
        discovered.append(repo_dir.name)
    return discovered


def _repository_cwd(
    workspace_manager,
    task_id: str,
    repo_id: str,
) -> str | None:
    """Resolve <workspace>/<task>/<repo>/ as a cwd, validating it exists."""
    if workspace_manager is None or not repo_id:
        return None
    try:
        path = workspace_manager.repository_path(task_id, repo_id)
    except Exception:
        return None
    return str(path) if path.is_dir() else None


def _workspace_status(workspace_manager, task_id: str) -> str:
    """Return the workspace's current status (provisioning|active|review|done|...).

    Empty string when the workspace manager isn't wired or the task has
    no record. Used by the diff endpoint so the UI can label diffs that
    are *already pushed* differently from in-flight changes.
    """
    if workspace_manager is None:
        return ''
    try:
        record = workspace_manager.get(task_id)
    except Exception:
        return ''
    if record is None:
        return ''
    return str(getattr(record, 'status', '') or '')


def _compute_repo_diff(
    repo_id: str,
    cwd: str,
    *,
    task_id: str = '',
    agent_service=None,
) -> dict[str, Any]:
    """Build the per-repo diff payload the Changes-tab accordion expects.

    A per-task workspace clone is supposed to live on the task branch
    (named after ``task_id`` per kato's branch-naming convention). If
    the clone has drifted to ``master`` for any reason, opening the
    Changes tab self-heals it by checking out the task branch first
    — otherwise the operator would see "No changes between master
    and master" and have no way to fix it from the UI.

    Base branch resolution: ALWAYS prefer the kato config's
    ``destination_branch`` for the repo. Kato forks every task
    branch from that ref, so ``git diff <task_branch>...origin/<base>``
    only makes sense when ``<base>`` matches the configured value.
    Auto-detecting via git (``origin/HEAD``) returns the *remote's*
    default branch, which is a different thing — we hit a real bug
    where a repo with default ``master`` but configured base
    ``develop`` had the Changes tab show hundreds of unrelated
    commits because the diff was computed against the wrong base.
    Git detection only kicks in as a last-resort fallback when the
    inventory cannot answer (e.g. unknown repo id).

    Failures (e.g. a repo where ``origin/<base>`` isn't reachable)
    surface as an ``error`` field so the UI can render that single
    accordion section in an error state without breaking the rest.
    """
    if task_id:
        ensure_branch_checked_out(cwd, task_id)
    base = _resolve_diff_base(repo_id, cwd, agent_service)
    if not base:
        return {
            'repo_id': repo_id,
            'cwd': cwd,
            'base': '',
            'head': '',
            'diff': '',
            'error': _no_base_error_message(repo_id),
        }
    # Conflicted file list — surfaces in the Changes tab as a yellow
    # CONFLICTED badge and in the Files tree as a warning icon. Best-
    # effort: an empty list is the common (no-conflict) case AND the
    # error case; the rest of the payload is unaffected either way.
    return {
        'repo_id': repo_id,
        'cwd': cwd,
        'base': base,
        'head': current_branch(cwd),
        'diff': diff_against_base(cwd, f'origin/{base}'),
        'conflicted_files': conflicted_paths(cwd),
        'error': '',
    }


def _resolve_diff_base(repo_id: str, cwd: str, agent_service) -> str:
    """Configured destination_branch first, then git auto-detect.

    Pulled out of ``_compute_repo_diff`` so the resolution policy
    is in one named place — both the diff endpoint AND the commits
    endpoint share it.
    """
    if repo_id and agent_service is not None:
        lookup = getattr(agent_service, 'configured_destination_branch', None)
        if callable(lookup):
            configured = (lookup(repo_id) or '').strip()
            if configured:
                return configured
    return detect_default_branch(cwd)


def _no_base_error_message(repo_id: str) -> str:
    """Operator-facing message when no diff base can be resolved."""
    if repo_id:
        return (
            f'no destination_branch configured for repository {repo_id!r} '
            f'in your kato config, and the workspace clone has no '
            f'``origin/HEAD`` set either. Add a ``destination_branch`` '
            f'entry under that repo in your kato config and restart '
            f'kato (or run ``git remote set-head origin --auto`` in the '
            f'workspace clone if you cannot edit the config).'
        )
    return (
        'no destination branch configured and could not detect one '
        'from the workspace clone — check your kato config.'
    )


def _resolve_repo_cwd(
    session_manager,
    workspace_manager,
    task_id: str,
    repo_id: str,
) -> str | None:
    """Pick the cwd to inspect for the Files / Changes panes.

    When ``repo_id`` is provided, point at that workspace clone so the
    UI can switch between every repo a multi-repo task touches. Empty
    ``repo_id`` falls back to the session record (legacy single-repo
    flows and tabs that haven't yet picked one).
    """
    if repo_id:
        cwd = _repository_cwd(workspace_manager, task_id, repo_id)
        if cwd is not None:
            return cwd
    return _record_cwd_or_none(session_manager, task_id)


# Branch-safety lock is gone in workspace mode: each task has its own
# clone, so there's no shared HEAD that another task could drift away
# under. Kept the helper out of the import surface; the SSE generator
# below no longer emits ``branch_state`` events and POST handlers no
# longer 409 on branch divergence.


def create_app(
    *,
    session_manager=None,
    workspace_manager=None,
    planning_session_runner=None,
    fallback_state_dir: str = '',
    status_broadcaster=None,
    agent_service=None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / 'templates'),
        static_folder=str(REPO_ROOT / 'static'),
    )
    if session_manager is None:
        session_manager = _build_fallback_manager(fallback_state_dir)
    app.config['SESSION_MANAGER'] = session_manager
    app.config['WORKSPACE_MANAGER'] = workspace_manager
    app.config['PLANNING_SESSION_RUNNER'] = planning_session_runner
    app.config['STATUS_BROADCASTER'] = status_broadcaster
    app.config['AGENT_SERVICE'] = agent_service

    _register_http_routes(app)
    _register_streaming_routes(app)
    _register_status_routes(app)
    return app


# ----- HTTP routes -----


def _register_http_routes(app: Flask) -> None:

    @app.get('/')
    def index() -> str:
        # Minimal HTML shell — the React bundle fetches /api/sessions
        # itself and re-renders on every poll, so server-side template
        # rendering of the tab list is gone.
        return render_template('index.html')

    @app.get('/api/sessions')
    def list_sessions():
        return jsonify(_records_as_dicts(
            app.config['SESSION_MANAGER'],
            app.config.get('WORKSPACE_MANAGER'),
            app.config.get('AGENT_SERVICE'),
        ))

    @app.get('/api/sessions/<task_id>')
    def get_session(task_id: str):
        manager = app.config['SESSION_MANAGER']
        record = manager.get_record(task_id)
        if record is None:
            return jsonify({'error': 'session not found'}), 404
        payload = _record_to_dict(record)
        session = manager.get_session(task_id)
        payload['live'] = session is not None and session.is_alive
        if session is not None:
            payload['recent_events'] = [
                event.to_dict() for event in session.recent_events()
            ]
        else:
            payload['recent_events'] = []
        return jsonify(payload)

    @app.get('/api/claude/sessions')
    def list_claude_sessions():
        """List Claude Code sessions available for adoption.

        Reads ``~/.claude/projects/`` (or ``KATO_CLAUDE_SESSIONS_ROOT``
        for tests) and returns every transcript with metadata: cwd,
        last-modified epoch, turn count, and first/last user-message
        previews. The UI dropdown sorts by recency and lets the
        operator pick one to adopt for a task.

        Query string ``q=<text>`` filters by case-insensitive substring
        match against cwd and either preview. Empty ``q`` returns all
        (capped server-side).
        """
        from claude_core_lib.claude_core_lib.session.index import (
            list_sessions as list_claude_session_metadata,
        )

        query = request.args.get('q', '') or ''
        rows = list_claude_session_metadata(query=query)
        # Mark sessions already adopted by a kato task so the UI can
        # warn before re-adoption. Cheap O(N*M) — N = sessions on
        # disk, M = task records — both small in practice.
        manager = app.config['SESSION_MANAGER']
        adopted_by: dict[str, str] = {}
        try:
            for record in manager.list_records():
                sid = str(getattr(record, 'claude_session_id', '') or '').strip()
                if sid and sid not in adopted_by:
                    adopted_by[sid] = record.task_id
        except Exception:  # pragma: no cover — defensive
            adopted_by = {}
        return jsonify({
            'sessions': [
                {
                    **row.to_dict(),
                    'adopted_by_task_id': adopted_by.get(row.session_id, ''),
                }
                for row in rows
            ],
        })

    @app.post('/api/sessions/<task_id>/adopt-claude-session')
    def adopt_claude_session(task_id: str):
        """Bind an existing Claude Code session id to ``task_id``.

        Body: ``{"claude_session_id": "<uuid>"}``. The next agent
        spawn for ``task_id`` will ``--resume`` that session instead
        of starting a fresh conversation. Refuses when a live session
        is already running for ``task_id`` — the operator must close
        it first to avoid two writers on the same record.
        """
        payload = request.get_json(silent=True) or {}
        claude_session_id = str(payload.get('claude_session_id', '') or '').strip()
        if not claude_session_id:
            return jsonify({'error': 'claude_session_id is required'}), 400
        manager = app.config['SESSION_MANAGER']
        live_session = manager.get_session(task_id)
        if live_session is not None and live_session.is_alive:
            return jsonify({
                'error': (
                    'a live planning session is already running for this task; '
                    'stop it before adopting a different Claude session'
                ),
            }), 409
        try:
            record = manager.adopt_session_id(
                task_id,
                claude_session_id=claude_session_id,
            )
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        # Migrate the source JSONL into kato's per-task workspace
        # cwd so ``claude --resume <id>`` can find it. Kato spawns
        # Claude at its own workspace clone — NOT the source
        # session's cwd — so the operator can review changes against
        # an isolated worktree without risk of clobbering their VS
        # Code checkout. The trade-off is documented in
        # ``docs/adopting-existing-claude-sessions.md``: the migrated
        # JSONL is a one-time SNAPSHOT; turns the source instance
        # takes after adoption don't sync over.
        migration = _migrate_adopted_session_transcript(
            app, task_id, claude_session_id,
        )
        migration_path = str(migration) if migration else ''
        return jsonify({
            'task_id': record.task_id,
            'claude_session_id': record.claude_session_id,
            'transcript_migrated_to': migration_path,
        })

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok'}

    @app.get('/api/safety')
    def safety_state():
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            is_bypass_enabled,
            is_running_as_root,
        )
        return jsonify({
            'bypass_permissions': is_bypass_enabled(),
            'running_as_root': is_running_as_root(),
        })

    @app.get('/logo.png')
    def logo():
        candidate = KATO_REPO_ROOT / 'kato.png'
        if not candidate.exists():
            return ('logo not found', 404)
        return send_file(candidate, mimetype='image/png')

    @app.get('/favicon.png')
    def favicon_png():
        candidate = KATO_REPO_ROOT / 'kato.png'
        if not candidate.exists():
            return ('favicon not found', 404)
        response = send_file(candidate, mimetype='image/png')
        # Browsers cache favicons aggressively. Tell them to revalidate so
        # a fresh kato.png gets picked up without forcing the operator to
        # clear browser site data.
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return response

    @app.get('/favicon.ico')
    def favicon_ico():
        # Browsers probe /favicon.ico by default even without a <link>
        # tag. Serve the same PNG (mislabelled as image/x-icon is fine,
        # every browser kato targets honors the actual content).
        candidate = KATO_REPO_ROOT / 'kato.png'
        if not candidate.exists():
            return ('favicon not found', 404)
        response = send_file(candidate, mimetype='image/png')
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return response

    @app.get('/api/sessions/<task_id>/files')
    def list_session_files(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        repository_ids = _task_repository_ids(workspace_manager, task_id)
        # Multi-repo task: enumerate every clone so the UI can render
        # one tree per repo. Single-repo / legacy: fall back to the
        # session record cwd so the response shape is unchanged.
        if repository_ids:
            trees = []
            for repo_id in repository_ids:
                cwd = _repository_cwd(workspace_manager, task_id, repo_id)
                if cwd is None:
                    continue
                trees.append({
                    'repo_id': repo_id,
                    'cwd': cwd,
                    'tree': tracked_file_tree(cwd),
                    # Conflict markers — same source as the Changes
                    # tab. UI marks each path with a warning icon so
                    # the operator spots merge conflicts at a glance.
                    'conflicted_files': conflicted_paths(cwd),
                })
            if trees:
                return jsonify({
                    'repository_ids': [t['repo_id'] for t in trees],
                    'trees': trees,
                    # Back-compat: first repo doubles as the legacy
                    # ``cwd``/``tree`` pair so older clients still work.
                    'cwd': trees[0]['cwd'],
                    'tree': trees[0]['tree'],
                })
        cwd = _record_cwd_or_none(manager, task_id)
        if cwd is None:
            # Workspace clones already gone (task forgotten / never
            # provisioned). Return an empty payload with 200 instead
            # of 404 so the Files tab shows "no repositories" rather
            # than the scary "Error: session not found" the operator
            # sees right after kato finishes a publish.
            return jsonify({
                'repository_ids': [],
                'trees': [],
                'cwd': '',
                'tree': [],
                'conflicted_files': [],
            })
        legacy_tree = tracked_file_tree(cwd)
        legacy_conflicts = conflicted_paths(cwd)
        return jsonify({
            'repository_ids': [],
            'trees': [{
                'repo_id': '', 'cwd': cwd, 'tree': legacy_tree,
                'conflicted_files': legacy_conflicts,
            }],
            'cwd': cwd,
            'tree': legacy_tree,
            'conflicted_files': legacy_conflicts,
        })

    @app.get('/api/sessions/<task_id>/diff')
    def get_session_diff(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        agent_service = app.config.get('AGENT_SERVICE')
        workspace_status = _workspace_status(workspace_manager, task_id)
        repository_ids = _task_repository_ids(workspace_manager, task_id)
        # Multi-repo task: compute one diff per clone so the UI can
        # render accordions side by side. Single-repo / legacy path:
        # fall back to the session record cwd, same shape as before.
        if repository_ids:
            diffs = []
            for repo_id in repository_ids:
                cwd = _repository_cwd(workspace_manager, task_id, repo_id)
                if cwd is None:
                    continue
                diffs.append(_compute_repo_diff(
                    repo_id, cwd, task_id=task_id, agent_service=agent_service,
                ))
            if diffs:
                first = diffs[0]
                return jsonify({
                    'repository_ids': [d['repo_id'] for d in diffs],
                    'diffs': diffs,
                    'workspace_status': workspace_status,
                    # Back-compat scalar fields mirror the first repo.
                    'repo_id': first['repo_id'],
                    'base': first['base'],
                    'head': first['head'],
                    'diff': first['diff'],
                })
        cwd = _record_cwd_or_none(manager, task_id)
        if cwd is None:
            # Same rationale as the Files endpoint above: prefer an
            # empty diff payload over a 404 so the Changes tab shows
            # "No repositories for this task." instead of an error.
            return jsonify({
                'repository_ids': [],
                'diffs': [],
                'workspace_status': workspace_status,
                'repo_id': '',
                'base': '',
                'head': '',
                'diff': '',
            })
        single = _compute_repo_diff('', cwd, task_id=task_id, agent_service=agent_service)
        return jsonify({
            'repository_ids': [],
            'diffs': [single],
            'workspace_status': workspace_status,
            'repo_id': '',
            'base': single['base'],
            'head': single['head'],
            'diff': single['diff'],
        })

    @app.get('/api/sessions/<task_id>/commits')
    def list_repo_commits(task_id: str):
        """Recent commits on a repo's task branch (newest first).

        Drives the Files-tab "view changes from commit" dropdown
        on each repo's header. Required query param: ``repo``
        (the repository id, matching the ``files`` / ``diff``
        endpoints). Optional ``limit`` (default 50, capped at 200)
        for very long-running task branches.
        """
        repo_id = (request.args.get('repo') or '').strip()
        if not repo_id:
            return jsonify({'error': 'repo query parameter is required'}), 400
        try:
            limit = int(request.args.get('limit', '50'))
        except (TypeError, ValueError):
            limit = 50
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        agent_service = app.config.get('AGENT_SERVICE')
        cwd = _repository_cwd(workspace_manager, task_id, repo_id)
        if cwd is None:
            return jsonify({'error': f'repository {repo_id!r} not in workspace'}), 404
        # Same resolver as the diff endpoint — configured
        # destination_branch wins over git auto-detection so the
        # commit list matches what the operator sees in Changes.
        base = _resolve_diff_base(repo_id, cwd, agent_service)
        if not base:
            return jsonify({
                'commits': [],
                'error': _no_base_error_message(repo_id),
            }), 200
        commits = list_branch_commits(cwd, f'origin/{base}', limit=limit)
        return jsonify({
            'repo_id': repo_id,
            'base': base,
            'head': current_branch(cwd),
            'commits': commits,
        })

    @app.get('/api/sessions/<task_id>/commit')
    def get_repo_commit_diff(task_id: str):
        """Unified diff for a single commit on a repo.

        Required query params: ``repo`` (repository id) and ``sha``
        (the commit SHA returned by ``/commits``). The diff is the
        same shape as ``/diff`` so the existing ``react-diff-view``
        rendering works without changes.
        """
        repo_id = (request.args.get('repo') or '').strip()
        sha = (request.args.get('sha') or '').strip()
        if not repo_id:
            return jsonify({'error': 'repo query parameter is required'}), 400
        if not sha:
            return jsonify({'error': 'sha query parameter is required'}), 400
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        cwd = _repository_cwd(workspace_manager, task_id, repo_id)
        if cwd is None:
            return jsonify({'error': f'repository {repo_id!r} not in workspace'}), 404
        diff = diff_for_commit(cwd, sha)
        return jsonify({
            'repo_id': repo_id,
            'sha': sha,
            'diff': diff,
        })

    @app.post('/api/sessions/<task_id>/approve-push')
    def approve_task_push(task_id: str):
        """Operator approves the paused push for a ``kato:wait-before-git-push`` task."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        approve = getattr(agent_service, 'approve_push', None)
        if not callable(approve):
            return jsonify({'error': 'agent service does not support push approval'}), 501
        result = approve(task_id)
        if result is None:
            return jsonify({
                'approved': False,
                'task_id': task_id,
                'error': 'no pending publish for this task',
            }), 404
        return jsonify({'approved': True, 'task_id': task_id, 'result': result})

    @app.get('/api/sessions/<task_id>/awaiting-push-approval')
    def get_awaiting_push_approval(task_id: str):
        """UI uses this to decide whether to render the "Approve push" button."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'awaiting_push_approval': False, 'task_id': task_id})
        check = getattr(agent_service, 'is_awaiting_push_approval', None)
        if not callable(check):
            return jsonify({'awaiting_push_approval': False, 'task_id': task_id})
        return jsonify({
            'awaiting_push_approval': bool(check(task_id)),
            'task_id': task_id,
        })

    @app.post('/api/sessions/<task_id>/push')
    def push_task(task_id: str):
        """Operator-triggered push from the planning UI's ``Push`` button."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        push = getattr(agent_service, 'push_task', None)
        if not callable(push):
            return jsonify({'error': 'agent service does not support push'}), 501
        result = push(task_id) or {}
        if result.get('error') and not result.get('pushed'):
            return jsonify(result), 404 if 'no workspace' in str(result['error']) else 500
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/pull')
    def pull_task(task_id: str):
        """Operator-triggered fast-forward pull from the planning UI's
        ``Pull`` button. Symmetric to ``/push``."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        pull = getattr(agent_service, 'pull_task', None)
        if not callable(pull):
            return jsonify({'error': 'agent service does not support pull'}), 501
        result = pull(task_id) or {}
        if result.get('error') and not result.get('pulled'):
            return jsonify(result), 404 if 'no workspace' in str(result['error']) else 500
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/pull-request')
    def create_task_pull_request(task_id: str):
        """Operator-triggered PR open from the planning UI's ``Pull request`` button."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        create = getattr(agent_service, 'create_pull_request_for_task', None)
        if not callable(create):
            return jsonify({'error': 'agent service does not support PR creation'}), 501
        result = create(task_id) or {}
        if result.get('error') and not result.get('created'):
            return jsonify(result), 404 if 'no workspace' in str(result['error']) else 500
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/update-source')
    def update_task_source(task_id: str):
        """Push + sync the operator's REPOSITORY_ROOT_PATH clones to the
        task branch. Pure git plumbing — no AI involvement. Drives the
        planning UI's ``Update source`` button.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        update = getattr(agent_service, 'update_source_for_task', None)
        if not callable(update):
            return jsonify({'error': 'agent service does not support source-update'}), 501
        result = update(task_id) or {}
        if result.get('error') and not result.get('updated'):
            return jsonify(result), 404 if 'no workspace' in str(result['error']) else 500
        return jsonify(result)

    @app.get('/api/sessions/<task_id>/comments')
    def list_task_comments(task_id: str):
        """Every comment on the task workspace (optionally per-repo)."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        list_comments = getattr(agent_service, 'list_task_comments', None)
        if not callable(list_comments):
            return jsonify({'comments': []})
        repo_id = (request.args.get('repo') or '').strip()
        return jsonify({'comments': list_comments(task_id, repo_id)})

    @app.post('/api/sessions/<task_id>/comments')
    def create_task_comment(task_id: str):
        """Add a local comment + immediately kick / queue kato.

        Body: ``{repo, file_path, line?, body, parent_id?}``. ``line``
        defaults to -1 (file-level). ``parent_id`` makes this a reply
        — replies don't kick the agent (they're additional context;
        kato runs on top-of-thread).
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        add_comment = getattr(agent_service, 'add_task_comment', None)
        if not callable(add_comment):
            return jsonify({'error': 'comments not supported'}), 501
        payload = request.get_json(silent=True) or {}
        result = add_comment(
            task_id,
            repo_id=str(payload.get('repo') or '').strip(),
            file_path=str(payload.get('file_path') or '').strip(),
            line=int(payload.get('line', -1) or -1),
            body=str(payload.get('body') or ''),
            parent_id=str(payload.get('parent_id') or ''),
            author=str(payload.get('author') or ''),
        ) or {}
        if not result.get('ok'):
            err = str(result.get('error', 'add failed'))
            status = 404 if 'no workspace' in err else 400
            return jsonify(result), status
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/resolve')
    def resolve_task_comment(task_id: str, comment_id: str):
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        resolve = getattr(agent_service, 'resolve_task_comment', None)
        if not callable(resolve):
            return jsonify({'error': 'comments not supported'}), 501
        payload = request.get_json(silent=True) or {}
        return jsonify(resolve(
            task_id, comment_id,
            resolved_by=str(payload.get('resolved_by') or ''),
        ))

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/addressed')
    def mark_comment_addressed(task_id: str, comment_id: str):
        """Mark kato_status=ADDRESSED + post 'Kato addressed' on remote.

        Body (optional): ``{"addressed_sha": "<commit-sha>"}``.
        Called after a kato run produces a fix for the comment.
        For remote-sourced comments, also posts the standard
        "Kato addressed this review comment and pushed a follow-up
        update" reply on the source git platform.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        mark = getattr(agent_service, 'mark_comment_addressed', None)
        if not callable(mark):
            return jsonify({'error': 'comments not supported'}), 501
        payload = request.get_json(silent=True) or {}
        return jsonify(mark(
            task_id, comment_id,
            addressed_sha=str(payload.get('addressed_sha') or ''),
        ))

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/reopen')
    def reopen_task_comment(task_id: str, comment_id: str):
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        reopen = getattr(agent_service, 'reopen_task_comment', None)
        if not callable(reopen):
            return jsonify({'error': 'comments not supported'}), 501
        return jsonify(reopen(task_id, comment_id))

    @app.delete('/api/sessions/<task_id>/comments/<comment_id>')
    def delete_task_comment(task_id: str, comment_id: str):
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        delete = getattr(agent_service, 'delete_task_comment', None)
        if not callable(delete):
            return jsonify({'error': 'comments not supported'}), 501
        return jsonify(delete(task_id, comment_id))

    @app.post('/api/sessions/<task_id>/comments/sync')
    def sync_task_comments(task_id: str):
        """Pull remote PR comments + ``git pull`` the workspace clone."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        sync = getattr(agent_service, 'sync_remote_comments', None)
        if not callable(sync):
            return jsonify({'error': 'comments not supported'}), 501
        payload = request.get_json(silent=True) or {}
        repo_id = str(payload.get('repo') or '').strip()
        if not repo_id:
            return jsonify({'ok': False, 'error': 'repo is required'}), 400
        return jsonify(sync(task_id, repo_id))

    @app.get('/api/tasks')
    def list_all_tasks():
        """Every task assigned to kato, regardless of state.

        Drives the planning UI's "+ Add task" picker on the left
        panel. Includes open / in-progress / in-review / done so
        the operator can pick anything they own.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        list_tasks = getattr(agent_service, 'list_all_assigned_tasks', None)
        if not callable(list_tasks):
            return jsonify({'tasks': []})
        return jsonify({'tasks': list_tasks()})

    @app.post('/api/tasks/<task_id>/adopt')
    def adopt_task(task_id: str):
        """Pull a task into kato: provision a workspace + clones.

        Mirrors the autonomous initial-task path's first three
        steps (resolve repos → REP gate → workspace clones) so the
        adopted task lands with the same on-disk shape kato's queue
        scan would produce. No agent spawn — the operator types
        into the chat tab when ready.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        adopt = getattr(agent_service, 'adopt_task', None)
        if not callable(adopt):
            return jsonify({'error': 'agent service does not support adopt_task'}), 501
        result = adopt(task_id) or {}
        if result.get('error') and not result.get('adopted'):
            err = str(result.get('error', ''))
            status = 404 if 'not assigned' in err else 500
            if 'restricted execution protocol' in err:
                status = 403
            return jsonify(result), status
        return jsonify(result)

    @app.get('/api/repositories')
    def list_inventory_repositories():
        """Return the list of repos kato knows about — the chooser source.

        Drives the Files-tab "+ Add repository" picker. Filtering
        ("which of these are already on this task") happens UI-side
        so the same payload can power other chooser UIs without
        re-fetching per task.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        list_repos = getattr(agent_service, 'list_inventory_repositories', None)
        if not callable(list_repos):
            return jsonify({'repositories': []})
        return jsonify({'repositories': list_repos()})

    @app.post('/api/sessions/<task_id>/add-repository')
    def add_task_repository(task_id: str):
        """Tag the task with ``kato:repo:<id>`` and clone the repo.

        Body: ``{"repository_id": "<inventory-id>"}``. Combines the
        platform-side tag write with the workspace-side clone in one
        call so the operator can attach a new repo without bouncing
        through YouTrack / Jira and the Sync button.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        add_repo = getattr(agent_service, 'add_task_repository', None)
        if not callable(add_repo):
            return jsonify({'error': 'agent service does not support add-repository'}), 501
        payload = request.get_json(silent=True) or {}
        repository_id = str(payload.get('repository_id', '') or '').strip()
        if not repository_id:
            return jsonify({'error': 'repository_id is required'}), 400
        result = add_repo(task_id, repository_id) or {}
        if result.get('error') and not result.get('added'):
            err = str(result.get('error', ''))
            status = 404 if 'not in the kato inventory' in err else 500
            return jsonify(result), status
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/sync-repositories')
    def sync_task_repositories(task_id: str):
        """Add any task repos missing from the workspace; never remove.

        Drives the Files-tab "Sync repositories" icon. Reads the
        ticket platform's view of the task (its tags + description),
        resolves the full repo set, and clones any that aren't yet
        on disk. Already-cloned repos and repos that are on disk but
        no longer on the task are LEFT ALONE — sync is purely
        additive.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        sync = getattr(agent_service, 'sync_task_repositories', None)
        if not callable(sync):
            return jsonify({'error': 'agent service does not support repo sync'}), 501
        result = sync(task_id) or {}
        if result.get('error') and not result.get('synced'):
            err = str(result.get('error', ''))
            status = 404 if 'no workspace' in err else 500
            return jsonify(result), status
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/finish')
    def finish_task(task_id: str):
        """Operator-triggered "I'm done" — same flow Claude triggers via
        the ``<KATO_TASK_DONE>`` sentinel. Pushes pending changes, opens
        a PR if none exists, and moves the ticket to In Review.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        finish = getattr(agent_service, 'finish_task_planning_session', None)
        if not callable(finish):
            return jsonify({'error': 'agent service does not support finish'}), 501
        result = finish(task_id) or {}
        if result.get('error') and not result.get('finished'):
            return jsonify(result), 500
        return jsonify(result)

    @app.get('/api/sessions/<task_id>/publish-state')
    def get_task_publish_state(task_id: str):
        """UI poll: drives the disabled state of the Push / Pull request buttons."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({
                'has_workspace': False,
                'has_pull_request': False,
                'task_id': task_id,
            })
        check = getattr(agent_service, 'task_publish_state', None)
        if not callable(check):
            return jsonify({
                'has_workspace': False,
                'has_pull_request': False,
                'task_id': task_id,
            })
        state = check(task_id) or {}
        state['task_id'] = task_id
        return jsonify(state)

    @app.delete('/api/sessions/<task_id>/workspace')
    def forget_task_workspace(task_id: str):
        """Manual escape hatch: wipe ``~/.kato/workspaces/<task_id>/``.

        Used by the "Forget this task" button on a task tab when kato's
        cleanup loop hasn't run yet (e.g. the ticket is still in the
        watched states but the operator knows the task is done).
        """
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'error': 'workspace manager not wired'}), 503
        try:
            workspace_manager.delete(task_id)
            return jsonify({'forgotten': True, 'task_id': task_id})
        except Exception as exc:
            return jsonify({
                'forgotten': False,
                'task_id': task_id,
                'error': str(exc),
            }), 500


# ----- live status feed (SSE) -----


def _register_status_routes(app: Flask) -> None:

    @app.get('/api/status/recent')
    def status_recent():
        broadcaster = app.config.get('STATUS_BROADCASTER')
        if broadcaster is None:
            return jsonify({'entries': [], 'latest_sequence': 0})
        return jsonify({
            'entries': [entry.to_dict() for entry in broadcaster.recent()],
            'latest_sequence': broadcaster.latest_sequence(),
        })

    @app.get('/api/status/events')
    def status_events_stream():
        broadcaster = app.config.get('STATUS_BROADCASTER')
        if broadcaster is None:
            # Stream a single "disabled" event then close so the UI can
            # render a tasteful "no live feed" line instead of waiting.
            def _empty():
                yield _sse_message(SSE_EVENT_STATUS_DISABLED, {})
            return Response(
                stream_with_context(_empty()),
                mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache, no-transform'},
            )
        return Response(
            stream_with_context(_status_event_stream(broadcaster)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
            },
        )


def _status_event_stream(broadcaster):
    """Yield SSE frames for live kato status entries.

    Pushes the buffered backlog up front (so a freshly-connecting browser
    sees the last 500 lines), then long-polls the broadcaster's condition
    variable for new entries. A periodic SSE comment keeps the connection
    alive through proxies that idle out silent streams.
    """
    # Flush response headers immediately so the browser's EventSource
    # transitions out of CONNECTING the moment it subscribes — otherwise
    # a freshly-booted kato with an empty broadcaster backlog leaves the
    # status bar stuck on "Connecting to kato…" until the first heartbeat.
    yield ': open\n\n'
    backlog = broadcaster.recent()
    last_sequence = backlog[-1].sequence if backlog else 0
    if backlog:
        for entry in backlog:
            yield _sse_message(SSE_EVENT_STATUS_ENTRY, entry.to_dict())
    else:
        # Empty backlog: synthesize a non-broadcaster entry so the UI
        # has *something* to render and never sits on "Connecting…".
        # The string sentinel keeps it distinct from any sequence number
        # the broadcaster will ever produce; the JS dedupe set treats
        # it as a normal key.
        yield _sse_message(SSE_EVENT_STATUS_ENTRY, {
            'sequence': 'synthetic-open',
            'epoch': time.time(),
            'level': 'INFO',
            'logger': 'webserver',
            'message': 'Live feed connected. Waiting for the first scan tick.',
        })
    last_heartbeat = time.monotonic()
    while True:
        new_entries = broadcaster.wait_for_new(
            since_sequence=last_sequence,
            timeout=_SSE_HEARTBEAT_SECONDS,
        )
        for entry in new_entries:
            yield _sse_message(SSE_EVENT_STATUS_ENTRY, entry.to_dict())
            last_sequence = entry.sequence
        if not new_entries and time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
            yield ': ping\n\n'
            last_heartbeat = time.monotonic()


# ----- streaming routes (SSE + POST) -----


def _register_streaming_routes(app: Flask) -> None:
    """Register every per-task chat / SSE / control endpoint.

    Each route is wired by its own focused registrar so this function
    stays a flat checklist instead of a god-handler. Want to add a new
    streaming endpoint? Add a registrar next to the others and call it
    from here.
    """
    _register_session_events_route(app)
    _register_post_message_route(app)
    _register_stop_session_route(app)
    _register_post_permission_route(app)


def _register_session_events_route(app: Flask) -> None:
    @app.get('/api/sessions/<task_id>/events')
    def session_events_stream(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        return Response(
            stream_with_context(
                _event_stream_generator(manager, workspace_manager, task_id),
            ),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
            },
        )


def _register_post_message_route(app: Flask) -> None:
    @app.post('/api/sessions/<task_id>/messages')
    def post_message(task_id: str):
        payload = request.get_json(silent=True) or {}
        text = str(payload.get('text', '') or '').strip()
        images = payload.get('images') or []
        if not isinstance(images, list):
            images = []
        if not text and not images:
            return jsonify({'error': 'text or images is required'}), 400
        manager = app.config['SESSION_MANAGER']
        delivered = _deliver_to_live_session(manager, task_id, text, images)
        if delivered is not None:
            return delivered
        # Respawn paths don't currently carry images — kato spawns
        # via ``--resume`` with the text as the first prompt, and
        # the session manager builds its own initial-prompt envelope.
        # Surfacing images here would require reshaping the runner's
        # ``resume_session_for_chat`` API. Defer until the operator
        # actually hits the idle-respawn-with-images case.
        return _spawn_or_reject_chat_session(app, task_id, text)


def _register_stop_session_route(app: Flask) -> None:
    @app.post('/api/sessions/<task_id>/stop')
    def stop_session(task_id: str):
        manager = app.config['SESSION_MANAGER']
        if manager.get_record(task_id) is None:
            return jsonify({'error': 'session not found'}), 404
        try:
            manager.terminate_session(task_id)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'status': 'stopped'})


def _register_post_permission_route(app: Flask) -> None:
    @app.post('/api/sessions/<task_id>/permission')
    def post_permission(task_id: str):
        session, error = _resolve_writable_session(
            app.config['SESSION_MANAGER'], task_id,
        )
        if error is not None:
            return error
        payload = request.get_json(silent=True) or {}
        request_id = str(payload.get('request_id', '') or '').strip()
        if not request_id:
            return jsonify({'error': 'request_id is required'}), 400
        try:
            session.send_permission_response(
                request_id=request_id,
                allow=bool(payload.get('allow', False)),
                rationale=str(payload.get('rationale', '') or ''),
            )
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'status': 'delivered'})


def _deliver_to_live_session(
    manager, task_id: str, text: str, images=None,
):
    """Send the user message to a live subprocess if one is running.

    Returns the Flask response on hit (delivered or 500 on send error)
    or ``None`` to signal the caller to fall through to the respawn
    path. Keeping this branch out of the route handler lets the
    "resume on idle" logic live in its own helper too.

    ``images`` is an optional list of ``{media_type, data}`` dicts —
    base64-encoded screenshots / pasted images. Forwarded as
    Anthropic image content blocks alongside the text.
    """
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not session.is_alive:
        return None
    try:
        session.send_user_message(text, images=images or [])
    except TypeError:
        # Older session implementation without an ``images`` kwarg —
        # fall back to text-only so a stale dependency doesn't break
        # the message path entirely.
        try:
            session.send_user_message(text)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    return jsonify({
        'status': 'delivered',
        'text': text,
        'image_count': len(images or []),
    })


def _spawn_or_reject_chat_session(app: Flask, task_id: str, text: str):
    """Lazy-respawn for idle tabs, or 409 if no runner is wired.

    Hits when the live-session path returned None — i.e. the tab is
    real but the subprocess has exited. Spawns a fresh Claude with
    ``--resume`` so the conversation continues without losing context.
    """
    runner = app.config.get('PLANNING_SESSION_RUNNER')
    if runner is None:
        return jsonify({'error': 'session is not running'}), 409
    manager = app.config['SESSION_MANAGER']
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    cwd, summary = _chat_resume_context(manager, workspace_manager, task_id)
    additional_dirs = _chat_additional_dirs(workspace_manager, task_id, cwd)
    try:
        runner.resume_session_for_chat(
            task_id=task_id,
            message=text,
            cwd=cwd,
            task_summary=summary,
            additional_dirs=additional_dirs,
        )
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    return jsonify({'status': 'spawned', 'text': text})


def _migrate_adopted_session_transcript(
    app, task_id: str, claude_session_id: str,
):
    """Copy the adopted session JSONL into kato's workspace cwd.

    Claude Code's session storage is keyed by cwd
    (``~/.claude/projects/<encoded-cwd>/<id>.jsonl``); ``--resume <id>``
    only finds the transcript if it lives under the SPAWN cwd's
    project directory. The dev's VS Code session was recorded against
    the dev's checkout path; kato spawns Claude at its per-task
    workspace clone — different paths. Without this copy, the next
    spawn silently starts a fresh conversation even though we passed
    ``--resume``. Returns the destination path or ``None``.
    """
    from claude_core_lib.claude_core_lib.session.index import (
        list_sessions,
        migrate_session_to_workspace,
    )

    session_manager = app.config['SESSION_MANAGER']
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    target_cwd, _summary = _chat_resume_context(
        session_manager, workspace_manager, task_id,
    )
    if not target_cwd:
        return None
    transcript_path = ''
    for entry in list_sessions(max_results=10000):
        if entry.session_id == claude_session_id:
            transcript_path = entry.transcript_path
            break
    if not transcript_path:
        return None
    return migrate_session_to_workspace(
        transcript_path=transcript_path,
        target_cwd=target_cwd,
    )


def _chat_resume_context(session_manager, workspace_manager, task_id: str) -> tuple[str, str]:
    """Best-effort lookup of cwd + summary for a chat-respawn.

    Falls back across managers because either side might be missing
    (kato/sessions wiped, or workspace metadata not yet populated).
    """
    cwd = ''
    summary = ''
    if session_manager is not None:
        try:
            record = session_manager.get_record(task_id)
        except Exception:
            record = None
        if record is not None:
            cwd = str(getattr(record, 'cwd', '') or '')
            summary = str(getattr(record, 'task_summary', '') or '')
    if workspace_manager is not None:
        try:
            workspace = workspace_manager.get(task_id)
        except Exception:
            workspace = None
        if workspace is not None:
            cwd = cwd or str(getattr(workspace, 'cwd', '') or '')
            summary = summary or str(getattr(workspace, 'task_summary', '') or '')
            if not cwd and getattr(workspace, 'repository_ids', None):
                first_repo = workspace.repository_ids[0]
                try:
                    cwd = str(workspace_manager.repository_path(task_id, first_repo))
                except Exception:
                    cwd = ''
    return cwd, summary


def _chat_additional_dirs(workspace_manager, task_id: str, cwd: str) -> list[str]:
    """Return sibling-repo paths to expose to Claude via ``--add-dir``.

    The chat path used to spawn Claude with ONLY ``cwd`` accessible.
    For multi-repo tasks that meant the agent saw exactly one repo
    and refused cross-repo questions ("verify the front end" →
    "the frontend repo is forbidden") because the only frontend
    name it knew about came from ``KATO_IGNORED_REPOSITORY_FOLDERS``.

    Workspace mode: walk every repo folder under
    ``~/.kato/workspaces/<task>/`` and surface them all (skipping
    the cwd one — Claude already has that). Empty list when there's
    no workspace (e.g. adopted-cwd tasks that point at the dev's
    own checkout); the dev's sibling repos sit elsewhere on disk
    and we don't probe parent dirs blindly.
    """
    if workspace_manager is None or not task_id:
        return []
    try:
        workspace = workspace_manager.get(task_id)
    except Exception:
        return []
    if workspace is None:
        return []
    repository_ids = list(getattr(workspace, 'repository_ids', None) or [])
    normalized_cwd = str(cwd or '').strip().rstrip('/\\')
    extras: list[str] = []
    seen: set[str] = set()
    for repo_id in repository_ids:
        try:
            repo_path = str(workspace_manager.repository_path(task_id, repo_id))
        except Exception:
            continue
        if not repo_path:
            continue
        normalized_repo = repo_path.rstrip('/\\')
        # Skip the cwd entry (Claude already has it as its working
        # directory) and anything we've already added.
        if normalized_repo == normalized_cwd or normalized_repo in seen:
            continue
        seen.add(normalized_repo)
        extras.append(normalized_repo)
    return extras


def _resolve_writable_session(manager, task_id: str):
    """Return (session, None) if writable; (None, error_response) otherwise.

    In workspace mode each task has its own clone so the old
    branch-safety check is gone. The only failure mode left is "no
    live subprocess for this task" — happens when kato has finished /
    terminated the task but the tab is still rendered.
    """
    session = manager.get_session(task_id)
    if session is None or not session.is_alive:
        return None, (jsonify({'error': 'session is not running'}), 409)
    return session, None


def _event_stream_generator(manager, workspace_manager, task_id: str):
    """Yield SSE frames for one tab's session.

    Lifecycle outcomes:
      * `session_missing`  — no record AND no workspace exists for this task.
      * `session_idle`     — workspace/record exists but no live subprocess
        (history replayed from Claude's JSONL so the chat shows past turns).
      * (live stream + `session_closed`) — events flow until the
        subprocess exits and the buffer drains.
    """
    claude_session_id = _resolve_claude_session_id(
        manager, workspace_manager, task_id,
    )
    record = manager.get_record(task_id) if manager is not None else None
    workspace = (
        workspace_manager.get(task_id)
        if workspace_manager is not None
        else None
    )
    if record is None and workspace is None:
        yield _sse_message(SSE_EVENT_SESSION_MISSING, {})
        return
    session = manager.get_session(task_id) if manager is not None else None
    if session is None:
        yield from _replay_preflight_log(workspace_manager, task_id)
        yield from _replay_history_from_disk(claude_session_id)
        idle_payload = _record_to_dict(record) if record is not None else {}
        yield _sse_message(SSE_EVENT_SESSION_IDLE, idle_payload)
        return
    yield from _replay_preflight_log(workspace_manager, task_id)
    yield from _replay_history_from_disk(claude_session_id)
    replayed_count = yield from _replay_session_backlog(session)
    yield from _follow_live_session(session, start_index=replayed_count)


def _replay_preflight_log(workspace_manager, task_id: str):
    """Yield ``system { subtype: 'preflight' }`` events from the workspace's
    preflight log so the chat tab shows clone progress (``cloning 1/3:
    admin-client``, ``✓ cloned 1/3: admin-client``, ``✓ all 3 cloned —
    starting agent``). The log lives at
    ``<workspace>/.kato-preflight.log`` and is appended to as the
    workspace provisioner runs; replaying it here is what surfaces
    "kato is cloning" in the chat instead of only the right-pane
    activity feed.

    Best-effort: missing log / missing workspace_manager / unreadable
    file all degrade silently — the chat loads with whatever else is
    available (history-from-disk, idle, etc.).
    """
    if workspace_manager is None or not task_id:
        return
    read = getattr(workspace_manager, 'read_preflight_log', None)
    if not callable(read):
        return
    try:
        entries = read(task_id)
    except Exception:
        return
    for epoch, message in entries:
        # ``subtype: 'preflight'`` is what the SSE-history reducer in
        # ``useSessionStream.js`` keys on to render these as system
        # bubbles. We use ``received_at_epoch=0`` so the dedupe path
        # treats them as archival history (same shape as the
        # ``_replay_history_from_disk`` events). If a future tail
        # mode wants to stream these live, swap to a real epoch.
        raw = {
            'type': 'system',
            'subtype': 'preflight',
            'message': message,
            'logged_at_epoch': epoch,
        }
        yield _sse_message(
            SSE_EVENT_SESSION_HISTORY_EVENT,
            {'event': {'received_at_epoch': 0, 'raw': raw}},
        )


def _resolve_claude_session_id(manager, workspace_manager, task_id: str) -> str:
    if manager is not None:
        try:
            record = manager.get_record(task_id)
        except Exception:
            record = None
        if record is not None and getattr(record, 'claude_session_id', ''):
            return str(record.claude_session_id)
    if workspace_manager is not None:
        try:
            workspace = workspace_manager.get(task_id)
        except Exception:
            workspace = None
        if workspace is not None and getattr(workspace, 'claude_session_id', ''):
            return str(workspace.claude_session_id)
    return ''


def _replay_history_from_disk(claude_session_id: str):
    if not claude_session_id:
        return
    try:
        from claude_core_lib.claude_core_lib.session.history import load_history_events
    except ImportError:
        return
    try:
        events = load_history_events(claude_session_id)
    except Exception:
        return
    # Emit under a distinct event type so the client doesn't run these
    # through the live-state reducer (otherwise an archived ``assistant``
    # event would set turnInFlight=true forever).
    for raw in events:
        yield _sse_message(
            SSE_EVENT_SESSION_HISTORY_EVENT,
            {'event': {'received_at_epoch': 0, 'raw': raw}},
        )


def _replay_session_backlog(session):
    """Catch a freshly-connecting browser up on everything seen so far."""
    backlog = session.recent_events()
    for event in backlog:
        yield _sse_message(SSE_EVENT_SESSION_EVENT, {'event': event.to_dict()})
    return len(backlog)


def _follow_live_session(session, start_index: int = 0):
    """Tail new events as they arrive, plus a periodic SSE heartbeat.

    Polls the session every ``_SSE_POLL_INTERVAL_SECONDS`` and yields
    the new tail via ``events_after`` (cheap O(new) slice instead of
    the old O(total) snapshot copy). 100ms of latency is invisible
    to humans and the per-tick cost is bounded — see the comment on
    ``_SSE_POLL_INTERVAL_SECONDS`` for why we are not doing the
    Condition-based blocking wait this used to do.
    """
    last_index = max(0, int(start_index or 0))
    last_heartbeat = time.monotonic()
    while True:
        new_events, last_index = session.events_after(last_index)
        for event in new_events:
            yield _sse_message(SSE_EVENT_SESSION_EVENT, {'event': event.to_dict()})

        if not session.is_alive:
            # Drain any final events that landed between the slice
            # and ``is_alive`` flipping, then close.
            tail, last_index = session.events_after(last_index)
            for event in tail:
                yield _sse_message(SSE_EVENT_SESSION_EVENT, {'event': event.to_dict()})
            yield _sse_message(SSE_EVENT_SESSION_CLOSED, {})
            return

        if time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
            yield ': ping\n\n'
            last_heartbeat = time.monotonic()

        time.sleep(_SSE_POLL_INTERVAL_SECONDS)


def _sse_message(event_type: str, data: dict[str, Any]) -> str:
    """Serialize one SSE message frame.

    Format follows the W3C SSE spec: an `event:` line names the event type
    (we route on this in JS), and a `data:` line carries the JSON payload.
    """
    body = dict(data)
    body['type'] = event_type
    return f'event: {event_type}\ndata: {json.dumps(body)}\n\n'


# ----- helpers -----


def _records_as_dicts(
    session_manager, workspace_manager, agent_service=None,
) -> list[dict[str, Any]]:
    """Tab list payload — one entry per known task.

    Source of truth: the workspace manager (folder-per-task). Each entry
    is enriched with ``live`` (is the Claude subprocess running?),
    ``claude_session_id`` (back-fill from session-manager records when
    older workspace metadata didn't capture it yet), and
    ``has_changes_pending`` (true when kato is paused awaiting push
    approval — the workspace has commits ready to push).
    """
    live_session_ids = _live_session_ids(session_manager)
    working_session_ids = _working_session_ids(session_manager)
    pending_permission_tool_by_task = _pending_permission_tool_by_task(session_manager)
    pending_permission_session_ids = set(pending_permission_tool_by_task.keys())
    if workspace_manager is None:
        return [
            _session_record_to_dict(
                record,
                live_session_ids,
                working_session_ids,
                pending_permission_session_ids,
                pending_permission_tool_by_task,
            )
            for record in session_manager.list_records()
        ]
    workspace_records = workspace_manager.list_workspaces()
    session_ids_by_task = _session_ids_by_task(session_manager)
    awaiting_push = getattr(agent_service, 'is_awaiting_push_approval', None)
    return [
        _workspace_record_to_dict(
            record,
            live_session_ids,
            session_ids_by_task,
            awaiting_push,
            working_session_ids=working_session_ids,
            pending_permission_session_ids=pending_permission_session_ids,
            pending_permission_tool_by_task=pending_permission_tool_by_task,
        )
        for record in workspace_records
    ]


def _session_record_to_dict(
    record,
    live_session_ids: set[str],
    working_session_ids: set[str],
    pending_permission_session_ids: set[str],
    pending_permission_tool_by_task: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = _record_to_dict(record)
    task_id = str(payload.get('task_id') or getattr(record, 'task_id', '') or '')
    payload['live'] = task_id in live_session_ids
    payload['working'] = task_id in working_session_ids
    payload['has_pending_permission'] = task_id in pending_permission_session_ids
    # The tool name on the most recent un-answered request — empty
    # string when nothing is pending. Lets the UI suppress tab
    # orange when the operator has a remembered "Allow always"
    # decision for that tool (auto-allow path will handle silently;
    # showing orange would be misleading).
    payload['pending_permission_tool_name'] = (pending_permission_tool_by_task or {}).get(task_id, '')
    return payload


def _session_ids_by_task(session_manager) -> dict[str, str]:
    if session_manager is None:
        return {}
    try:
        records = session_manager.list_records()
    except Exception:
        return {}
    return {
        str(record.task_id): str(getattr(record, 'claude_session_id', '') or '')
        for record in records
        if getattr(record, 'claude_session_id', '')
    }


def _working_session_ids(session_manager) -> set[str]:
    """Subset of ``_live_session_ids`` whose Claude turn is in flight.

    The sidebar tab dot uses this to dim a tab whose subprocess is alive
    but not actively producing — operator can tell at a glance whether
    Claude is still chewing on a turn or just waiting for input.
    """
    if session_manager is None:
        return set()
    try:
        records = session_manager.list_records()
    except Exception:
        return set()
    working: set[str] = set()
    for record in records:
        try:
            session = session_manager.get_session(record.task_id)
        except Exception:
            continue
        if session is not None and getattr(session, 'is_working', False):
            working.add(record.task_id)
    return working


def _pending_permission_session_ids(session_manager) -> set[str]:
    """Task ids whose live session is paused on an unanswered approval."""
    return set(_pending_permission_tool_by_task(session_manager).keys())


def _pending_permission_tool_by_task(session_manager) -> dict[str, str]:
    """Per-task ``{task_id: pending_tool_name}`` for the tab-attention path.

    Returns the tool name on the most recent unanswered permission
    request so the UI can decide whether to mark the tab orange. The
    tool name is the load-bearing piece: when the operator has a
    remembered "Allow always" decision for that tool, kato's
    PermissionDecisionContainer auto-submits silently and the tab
    SHOULDN'T go orange — without the tool name, the UI can't tell
    "Bash auto-handled" apart from "Edit waiting on a real ask" and
    flashes orange on every rapid-fire Bash request, which is the
    confused-operator UX in the reported screenshot.
    """
    if session_manager is None:
        return {}
    try:
        records = session_manager.list_records()
    except Exception:
        return {}
    pending: dict[str, str] = {}
    for record in records:
        try:
            session = session_manager.get_session(record.task_id)
        except Exception:
            continue
        if session is None:
            continue
        tool_name = _session_pending_permission_tool(session)
        if tool_name:
            # Empty-string tool name still marks pending (legacy
            # callers + back-compat) — the UI's filter just can't
            # match it to a remembered decision.
            pending[record.task_id] = tool_name
    return pending


def _session_has_pending_permission(session) -> bool:
    return bool(_session_pending_permission_tool(session))


def _session_pending_permission_tool(session) -> str:
    """Tool name on the live un-answered control request, or ''.

    Reads the streaming session's ``_pending_control_requests`` dict
    (via ``pending_control_request_tool()``). That dict is populated
    when a ``control_request`` event arrives and ``pop``'d when
    ``send_permission_response`` runs — so it flips false the
    instant the operator's reply (or auto-allow's reply) lands.
    Tab-orange tracks this; the operator never sees a stuck
    indicator from a request that's already been answered.

    Falls back to walking ``recent_events`` for sessions that
    don't expose the live state (older test stubs, or the
    permission_request shape that doesn't go through the
    control_request pipeline). The fallback was the only mode
    before — it sometimes left the orange "stuck" because a
    dedupe'd response or a request that the agent moved past
    without answering still appeared as the newest permission
    event in the history.
    """
    live_probe = getattr(session, 'pending_control_request_tool', None)
    if callable(live_probe):
        try:
            tool_name = str(live_probe() or '').strip()
        except Exception:
            tool_name = ''
        if tool_name:
            return tool_name
        # Live state says "nothing pending" — trust it. Don't fall
        # back to the history walk; that's what produced the stuck
        # orange in the first place.
        return ''
    # Legacy / test stub path: walk the history.
    for event in reversed(session.recent_events()):
        raw = getattr(event, 'raw', {}) or {}
        event_type = raw.get('type') if isinstance(raw, dict) else ''
        if event_type in (
            CLAUDE_EVENT_PERMISSION_REQUEST,
            CLAUDE_EVENT_CONTROL_REQUEST,
        ):
            tool_name = raw.get('tool_name') or raw.get('tool') or ''
            if not tool_name and isinstance(raw.get('request'), dict):
                nested = raw['request']
                tool_name = nested.get('tool_name') or nested.get('tool') or ''
            return str(tool_name or '<unknown>')
        if event_type in (CLAUDE_EVENT_PERMISSION_RESPONSE, CLAUDE_EVENT_RESULT):
            return ''
    return ''


def _live_session_ids(session_manager) -> set[str]:
    """Task ids that currently have an alive subprocess (best-effort)."""
    if session_manager is None:
        return set()
    try:
        records = session_manager.list_records()
    except Exception:
        return set()
    live: set[str] = set()
    for record in records:
        try:
            session = session_manager.get_session(record.task_id)
        except Exception:
            continue
        if session is not None and getattr(session, 'is_alive', False):
            live.add(record.task_id)
    return live


def _workspace_record_to_dict(
    record,
    live_session_ids: set[str],
    session_ids_by_task: dict[str, str] | None = None,
    awaiting_push_check=None,
    *,
    working_session_ids: set[str] | None = None,
    pending_permission_session_ids: set[str] | None = None,
    pending_permission_tool_by_task: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = record.to_dict() if hasattr(record, 'to_dict') else dict(record)
    payload['live'] = record.task_id in live_session_ids
    payload['working'] = (
        record.task_id in working_session_ids
        if working_session_ids is not None else False
    )
    payload['has_pending_permission'] = (
        record.task_id in pending_permission_session_ids
        if pending_permission_session_ids is not None else False
    )
    payload['pending_permission_tool_name'] = (
        (pending_permission_tool_by_task or {}).get(record.task_id, '')
    )
    if not payload.get('claude_session_id') and session_ids_by_task:
        backfilled = session_ids_by_task.get(record.task_id, '')
        if backfilled:
            payload['claude_session_id'] = backfilled
    has_pending = False
    if callable(awaiting_push_check):
        try:
            has_pending = bool(awaiting_push_check(record.task_id))
        except Exception:
            has_pending = False
    payload['has_changes_pending'] = has_pending
    return payload


def _record_to_dict(record) -> dict[str, Any]:
    if hasattr(record, 'to_dict'):
        return record.to_dict()
    if isinstance(record, dict):
        return record
    return {'task_id': str(getattr(record, 'task_id', '') or '')}


def _build_fallback_manager(fallback_state_dir: str):
    """Stand up a minimal manager so dev runs of the webserver don't crash."""
    try:
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
    except ImportError:
        from kato_webserver.session_registry import SessionRegistry

        class _RegistryAsManager:
            def __init__(self) -> None:
                self._registry = SessionRegistry()

            def list_records(self):
                return []

            def get_record(self, task_id: str):  # noqa: ARG002
                return None

            def get_session(self, task_id: str):  # noqa: ARG002
                return None

        return _RegistryAsManager()

    state_dir = (
        fallback_state_dir
        or os.environ.get('KATO_SESSION_STATE_DIR')
        or str(Path.home() / '.kato' / 'sessions')
    )
    return ClaudeSessionManager(state_dir=state_dir)


def main() -> None:
    """Run the dev server. Use kato.main for a real run with shared state."""
    app = create_app()
    host = os.environ.get('KATO_WEBSERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('KATO_WEBSERVER_PORT', '5050'))
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
