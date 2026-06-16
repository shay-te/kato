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
    POST /api/sessions/<task_id>/adopt-agent-session    — body keyed by AGENT_SESSION_ID
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
    POST /api/sessions/<task_id>/comments/<id>/edit     — edit queued local comment body / kato_status
    POST /api/sessions/<task_id>/comments/sync          — git pull + pull remote PR comments
    GET  /api/claude/sessions                           — list adoptable Claude Code sessions
    GET  /api/status/recent                             — recent kato-process log entries
    GET  /api/status/events                             — SSE: live kato-process log feed
"""

from __future__ import annotations

import json
import logging
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
    url_for,
)

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
    fix_session_id,
    read_session_id_from,
    same_session_id,
)
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping
from claude_core_lib.claude_core_lib.session.wire_protocol import (
    CLAUDE_EVENT_CONTROL_REQUEST,
    CLAUDE_EVENT_PERMISSION_REQUEST,
    CLAUDE_EVENT_PERMISSION_RESPONSE,
    CLAUDE_EVENT_RESULT,
    CLAUDE_SYSTEM_SUBTYPE_ACTION_GUARD_BLOCK,
    SSE_EVENT_SESSION_CLOSED,
    SSE_EVENT_SESSION_EVENT,
    SSE_EVENT_SESSION_HISTORY_EVENT,
    SSE_EVENT_SESSION_IDLE,
    SSE_EVENT_SESSION_MISSING,
    SSE_EVENT_STATUS_DISABLED,
    SSE_EVENT_STATUS_ENTRY,
)
from kato_webserver.git_diff_utils import (
    blob_size_at_ref,
    changed_paths,
    conflicted_paths,
    current_branch,
    detect_default_branch,
    diff_against_base,
    diff_for_commit,
    ensure_branch_checked_out,
    file_text_at_ref,
    list_branch_commits,
    tracked_file_tree,
)
from kato_webserver.prompt_draft_store import read_draft, write_draft


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
    """Repository ids for the task, merging metadata with what is on disk.

    Metadata order is preserved. Any repo directory found on disk that
    is not in the metadata list (e.g. manually cloned after the workspace
    was created, or added via a new YouTrack tag before sync ran) is
    appended at the end so the Files / Changes tabs pick it up immediately
    without requiring a sync or a reload.

    Falls back to the disk scan entirely when no workspace record exists —
    which happens after publish when the in-memory record is cleared but
    the on-disk clones are still present.
    """
    if workspace_manager is None:
        return []
    try:
        record = workspace_manager.get(task_id)
    except Exception:
        record = None
    meta_ids = []
    if record is not None:
        meta_ids = [
            str(repo_id)
            for repo_id in (getattr(record, 'repository_ids', []) or [])
            if repo_id
        ]
    disk_ids = _enumerate_repo_ids_from_disk(workspace_manager, task_id)
    if not meta_ids:
        return disk_ids
    meta_lower = {rid.lower() for rid in meta_ids}
    extras = [rid for rid in disk_ids if rid.lower() not in meta_lower]
    return meta_ids + extras


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


def _repo_relative_path(path_arg: str, cwd: str) -> str | None:
    """Normalize an API path into a repo-relative git path."""
    if not path_arg or path_arg == '/dev/null' or not cwd:
        return None
    raw = Path(path_arg)
    root = Path(cwd).resolve()
    try:
        candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    except (OSError, ValueError):
        return None
    if not _is_inside(candidate, root):
        return None
    rel = candidate.relative_to(root).as_posix()
    return rel or None


def _settings_env_path() -> Path:
    """Legacy ``<repo>/.env`` path — now only a READ fallback.

    The settings UI writes to ``~/.kato/settings.json`` (see
    ``kato_settings_store_utils``). ``.env`` is still read here so an
    operator who hasn't saved through the new UI yet still sees
    their existing values + a correct source label. The
    ``KATO_SETTINGS_ENV_FILE`` override is preserved for tests that
    pre-seed a fake ``.env`` fallback.
    """
    override = os.environ.get('KATO_SETTINGS_ENV_FILE', '').strip()
    if override:
        return Path(override)
    return KATO_REPO_ROOT / '.env'


def _resolve_setting(key: str) -> dict:
    """Resolve one settings key across all three stores.

    Precedence mirrors boot: live ``os.environ`` (shell or
    already-loaded) > ``~/.kato/settings.json`` > ``<repo>/.env``.
    Returns ``{value, source, value_from_file}`` where ``source`` is
    one of ``env`` / ``kato_settings`` / ``env_file`` / ``unset`` so
    the UI can label where a value lives.
    """
    from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

    live = os.environ.get(key, '')
    settings_value = read_kato_settings().get(key, '')
    env_file_value = _read_env_file_values(_settings_env_path()).get(key, '')
    if live:
        value, source = live, 'env'
    elif settings_value:
        value, source = settings_value, 'kato_settings'
    elif env_file_value:
        value, source = env_file_value, 'env_file'
    else:
        value, source = '', 'unset'
    return {
        'value': value,
        'source': source,
        'value_from_file': env_file_value,
    }


def _validate_settings(updates: dict[str, str]) -> list[str]:
    from kato_core_lib.helpers.kato_settings_schema_utils import (
        validate_settings_values,
    )
    return validate_settings_values(updates)


def _validate_persist_and_respond(updates: dict):
    """Validate → persist → standard "saved, restart required" response.

    Shared tail for the three settings-write POST handlers
    (task-providers / git-providers / all-settings). On validation
    failure returns the ``400`` ``'; '``-joined error; on a write
    ``OSError`` returns the ``500`` failure body; otherwise the
    ``{ok, updated_keys, restart_required, message}`` success envelope.
    Callers own their allowlist filtering and the "no recognised
    updates" empty-payload guard — this is purely the common tail.
    """
    validation_errors = _validate_settings(updates)
    if validation_errors:
        return jsonify({'error': '; '.join(validation_errors)}), 400
    try:
        _persist_settings(updates)
    except OSError as exc:
        return jsonify({'error': f'failed to write settings file: {exc}'}), 500
    return jsonify({
        'ok': True,
        'updated_keys': sorted(updates.keys()),
        'restart_required': True,
        'message': 'Saved. Restart kato for the change to take effect.',
    })


def _persist_settings(updates: dict) -> None:
    """Write UI-edited settings to ``~/.kato/settings.json`` (atomic).

    Single chokepoint so every settings route writes the same place.
    Replaces the old per-key ``.env`` writers.
    """
    from kato_core_lib.helpers.kato_settings_store_utils import write_kato_settings

    write_kato_settings(updates)


# ---------------------------------------------------------------------------
# Provider settings split into two concepts the operator thinks about
# separately:
#
#   * TASK provider — where tickets live + which one kato polls.
#     Drives ``KATO_ISSUE_PLATFORM``. Full field set (connection +
#     issue scoping + state transitions). One is "active".
#
#   * GIT host — where code + PRs live. kato infers the host from
#     each repo's remote URL, so there's NO "active" selector here;
#     this is purely "set the credentials kato uses to clone / push
#     / open PRs against <host>". Connection-level keys only.
#
# The same underlying ``.env`` keys back both — e.g. editing
# ``BITBUCKET_API_TOKEN`` in either tab writes the same line. That's
# intentional: the operator sees the key in whichever context they
# came looking for it.
# ---------------------------------------------------------------------------

# Task providers — ``POST /api/task-providers`` writes these +
# ``KATO_ISSUE_PLATFORM``. Adding a field / platform = edit here.
_TASK_PROVIDER_FIELDS: dict[str, tuple[str, ...]] = {
    'youtrack': (
        'YOUTRACK_API_BASE_URL',
        'YOUTRACK_API_TOKEN',
        'YOUTRACK_PROJECT',
        'YOUTRACK_ASSIGNEE',
        'YOUTRACK_PROGRESS_STATE_FIELD',
        'YOUTRACK_PROGRESS_STATE',
        'YOUTRACK_REVIEW_STATE_FIELD',
        'YOUTRACK_REVIEW_STATE',
        'YOUTRACK_ISSUE_STATES',
    ),
    'jira': (
        'JIRA_API_BASE_URL',
        'JIRA_API_TOKEN',
        'JIRA_EMAIL',
        'JIRA_PROJECT',
        'JIRA_ASSIGNEE',
        'JIRA_PROGRESS_STATE_FIELD',
        'JIRA_PROGRESS_STATE',
        'JIRA_REVIEW_STATE_FIELD',
        'JIRA_REVIEW_STATE',
        'JIRA_ISSUE_STATES',
    ),
    'github': (
        'GITHUB_API_BASE_URL',
        'GITHUB_API_TOKEN',
        'GITHUB_OWNER',
        'GITHUB_REPO',
        'GITHUB_ASSIGNEE',
        'GITHUB_PROGRESS_STATE_FIELD',
        'GITHUB_PROGRESS_STATE',
        'GITHUB_REVIEW_STATE_FIELD',
        'GITHUB_REVIEW_STATE',
        'GITHUB_ISSUE_STATES',
    ),
    'gitlab': (
        'GITLAB_API_BASE_URL',
        'GITLAB_API_TOKEN',
        'GITLAB_PROJECT',
        'GITLAB_ASSIGNEE',
        'GITLAB_PROGRESS_STATE_FIELD',
        'GITLAB_PROGRESS_STATE',
        'GITLAB_REVIEW_STATE_FIELD',
        'GITLAB_REVIEW_STATE',
        'GITLAB_ISSUE_STATES',
    ),
    'bitbucket': (
        'BITBUCKET_API_BASE_URL',
        'BITBUCKET_API_TOKEN',
        'BITBUCKET_USERNAME',
        'BITBUCKET_API_EMAIL',
        'BITBUCKET_WORKSPACE',
        'BITBUCKET_REPO_SLUG',
        'BITBUCKET_ASSIGNEE',
        'BITBUCKET_PROGRESS_STATE_FIELD',
        'BITBUCKET_PROGRESS_STATE',
        'BITBUCKET_REVIEW_STATE_FIELD',
        'BITBUCKET_REVIEW_STATE',
        'BITBUCKET_ISSUE_STATES',
    ),
}

# Git hosts — where code + PRs live. Only Bitbucket / GitHub /
# GitLab (YouTrack + Jira are pure trackers with no git). NO active
# selector: kato infers the host from each repo's remote URL, so
# this tab is "set the credentials kato uses to clone / push / open
# PRs against <host>". Connection-level keys only — issue scoping +
# state-transition fields belong on the Task provider tab.
_GIT_HOST_FIELDS: dict[str, tuple[str, ...]] = {
    'bitbucket': (
        'BITBUCKET_API_BASE_URL',
        'BITBUCKET_API_TOKEN',
        'BITBUCKET_USERNAME',
        'BITBUCKET_API_EMAIL',
        'BITBUCKET_WORKSPACE',
        'BITBUCKET_REPO_SLUG',
    ),
    'github': (
        'GITHUB_API_BASE_URL',
        'GITHUB_API_TOKEN',
        'GITHUB_OWNER',
        'GITHUB_REPO',
    ),
    'gitlab': (
        'GITLAB_API_BASE_URL',
        'GITLAB_API_TOKEN',
        'GITLAB_PROJECT',
    ),
}


def _filtered_provider_updates(
    field_map: dict[str, tuple[str, ...]], provider: str, fields,
) -> dict[str, str]:
    """Keep only ``fields`` entries whitelisted for ``provider``.

    Shared by the task-provider / git-host POST handlers: a payload
    can't smuggle env keys that don't belong to the named provider.
    Each kept value is coerced to ``str(value or '')`` (so ``None`` /
    missing becomes the empty string the ``.env`` layer expects).
    ``fields`` is expected to already be a validated dict.
    """
    allowed = set(field_map[provider])
    return {
        key: str(value or '')
        for key, value in fields.items()
        if key in allowed
    }


def _read_env_file_values(path: Path) -> dict[str, str]:
    """Parse a ``.env``-style file into a dict.

    Delegates to kato's single-source-of-truth parser
    (``parse_dotenv_text``) so the settings UI reads ``.env`` exactly
    the way kato's boot path does — ``KEY=value`` lines, a stripped
    leading ``export `` prefix, one matched pair of surrounding quotes
    removed, comments/blanks/malformed lines dropped, last duplicate
    key wins. Returns ``{}`` when the file is missing or unreadable.
    """
    from kato_core_lib.helpers.dotenv_utils import parse_dotenv_text

    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding='utf-8')
    except OSError:
        return {}
    return parse_dotenv_text(content)




def _file_preview_payload(size: int, content: str, base_fields: dict):
    """Binary-vs-text payload shaping for the ``/base-file`` preview.

    Given a file's ``size``, its decoded ``content`` string, and the
    route-specific ``base_fields`` (``{repo_id, path, base}``), returns
    the ``jsonify`` payload:

      * a NUL char in the first 8KB ⇒ ``{**base_fields, size, binary: True}``
      * otherwise ⇒ ``{**base_fields, size, binary: False, content}``

    The 1 MB cap is intentionally NOT here: both file routes guard on
    ``size`` *before* fetching content (``/file`` skips ``read_bytes``,
    ``/base-file`` skips ``file_text_at_ref``), so the cap can't move
    into a helper that already receives the content.

    ``/file`` deliberately does NOT route through this: it runs its NUL
    heuristic on the raw *bytes* (``b'\\x00' in raw[:8192]``) before
    decoding, and that 8KB window is measured in bytes — switching it to
    this string-based check would shift the binary/text boundary for the
    pathological case of a NUL byte sitting just past the 8KB mark behind
    a multibyte-heavy prefix. Kept byte-exact there.
    """
    if '\x00' in content[:8192]:
        return jsonify({**base_fields, 'size': size, 'binary': True})
    return jsonify({
        **base_fields,
        'size': size,
        'binary': False,
        'content': content,
    })


def _is_inside(candidate, root) -> bool:
    """True when ``candidate`` is at or under ``root`` (both pathlib.Path)."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


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


def _changed_files_for_repo(repo_id: str, cwd: str, agent_service) -> list[str]:
    """Changed-vs-base file list for the Files tree, base-resolved
    the same way the Changes tab does so the two never disagree.

    Read-only (no ``ensure_branch_checked_out``): the Files tab must
    not mutate git state. Empty list when the base can't be resolved
    — the tree just renders without change colouring.
    """
    base = _resolve_diff_base(repo_id, cwd, agent_service)
    if not base:
        return []
    return changed_paths(cwd, f'origin/{base}')


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


# Branch-safety lock is gone in workspace mode: each task has its own
# clone, so there's no shared HEAD that another task could drift away
# under. Kept the helper out of the import surface; the SSE generator
# below no longer emits ``branch_state`` events and POST handlers no
# longer 409 on branch divergence.


def _resolve_agent_method(
    app: Flask, method_name: str, *, not_callable_message: str = '',
):
    """Resolve a bound agent-service method, or the wiring/guard error.

    Returns ``(method, None)`` when the agent service is wired AND the
    named method is callable. Otherwise returns ``(None, error_response)``
    where the error is the standard ``503`` ("agent service not wired")
    or a ``501`` JSON envelope the ~16 publish/git/comment/task routes
    share.

    ``not_callable_message`` overrides the 501 body — each route keeps
    its own operator-facing phrasing (``does not support push`` vs
    ``comments not supported`` vs ``does not support PR creation``)
    verbatim. Defaults to ``agent service does not support <method>``.
    """
    agent_service = app.config.get('AGENT_SERVICE')
    if agent_service is None:
        return None, (jsonify({'error': 'agent service not wired'}), 503)
    method = getattr(agent_service, method_name, None)
    if not callable(method):
        message = (
            not_callable_message
            or f'agent service does not support {method_name}'
        )
        return None, (jsonify({'error': message}), 501)
    return method, None


def _envelope_response(result, success_key: str, *, missing_key: str = 'no workspace'):
    """Standard publish/git-route response: success key + substring status map.

    ``result`` is the agent-service return (coerced to ``{}`` when
    falsy). When it carries an ``error`` AND the operation did NOT
    succeed (``success_key`` falsy), the status is ``404`` if
    ``missing_key`` appears in the error string else ``500``. Otherwise
    the payload is returned with the default ``200``.
    """
    result = result or {}
    if result.get('error') and not result.get(success_key):
        status = 404 if missing_key in str(result['error']) else 500
        return jsonify(result), status
    return jsonify(result)


def _send_kato_png(*, cache_control: str = '', not_found_message: str = 'not found'):
    """Serve ``<kato-repo>/kato.png`` for the logo / favicon routes.

    ``cache_control`` (when non-empty) sets the response's
    ``Cache-Control`` header — the favicon routes ask browsers to
    revalidate so a fresh ``kato.png`` is picked up; the logo route
    leaves it unset. ``not_found_message`` is the ``404`` body string
    (``'logo not found'`` vs ``'favicon not found'``), kept verbatim
    per route.
    """
    candidate = KATO_REPO_ROOT / 'kato.png'
    if not candidate.exists():
        return (not_found_message, 404)
    response = send_file(candidate, mimetype='image/png')
    if cache_control:
        response.headers['Cache-Control'] = cache_control
    return response


def _get_task_override(app: Flask, key: str, task_id: str) -> str:
    """Read a per-task override (model / effort) from its config store.

    Treats a missing / ``None`` store as "no override" — returns the
    empty string, matching the GET routes' ``... or {}`` fallback.
    """
    return (app.config.get(key) or {}).get(task_id, '')


def _set_task_override(app: Flask, key: str, task_id: str, value: str = '') -> bool:
    """Write (or clear) a per-task override; ``False`` when not wired.

    A truthy ``value`` is stored; an empty ``value`` clears the
    override (``pop``, back to the configured default). Returns
    ``False`` when the store config slot is ``None`` so the caller can
    return its ``503 {'error': 'not available'}`` — distinct from the
    ``app.config.get(...) or {}`` read path, which must NOT mutate a
    fresh throwaway dict.
    """
    store = app.config.get(key)
    if store is None:
        return False
    if value:
        store[task_id] = value
    else:
        store.pop(task_id, None)
    return True


def create_app(
    *,
    session_manager=None,
    workspace_manager=None,
    planning_session_runner=None,
    fallback_state_dir: str = '',
    status_broadcaster=None,
    agent_service=None,
    force_scan_event=None,
    scan_in_progress_event=None,
    hook_runner=None,
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
    app.config['FORCE_SCAN_EVENT'] = force_scan_event
    app.config['SCAN_IN_PROGRESS_EVENT'] = scan_in_progress_event
    app.config['HOOK_RUNNER'] = hook_runner
    app.config['TASK_MODEL_OVERRIDES'] = {}
    # Per-task chat effort override (Claude ``--effort`` level), set from
    # the composer's effort selector. Empty/absent => the configured
    # default. Applied on (re)spawn of the chat session.
    app.config['TASK_EFFORT_OVERRIDES'] = {}

    # Cache-bust the unhashed static bundles. ``static/build/app.js``
    # and ``static/css/app.css`` keep fixed names across rebuilds, so
    # ``url_for('static', …)`` yields a stable URL the browser caches
    # forever — every UI change silently 304s to the old asset until a
    # manual hard-reload. Appending the file's mtime as ``?v=`` makes
    # the URL change whenever the file does, so a normal reload always
    # picks up a rebuilt bundle / edited CSS. Falls back to the plain
    # URL if the file is missing (e.g. before the first build).
    static_root = REPO_ROOT / 'static'

    @app.context_processor
    def _inject_asset_url():  # noqa: WPS430 (Flask requires a closure here)
        def asset_url(filename: str) -> str:
            url = url_for('static', filename=filename)
            try:
                version = int((static_root / filename).stat().st_mtime)
            except OSError:
                return url
            separator = '&' if '?' in url else '?'
            return f'{url}{separator}v={version}'

        return {'asset_url': asset_url}

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

    @app.get('/api/models')
    def list_models():
        # Discovered, not hardcoded: Claude serves the stable CLI aliases
        # (opus/sonnet/haiku) with live version labels when a credential is
        # available; Codex reads its own model cache. The picker can no longer
        # show a stale version (it used to hardcode "Opus 4.7").
        return jsonify({'models': _discover_chat_models(app)})

    @app.get('/api/openrouter/models')
    def list_openrouter_models():
        # Live OpenRouter catalogue for the settings ``OPENHANDS_LLM_MODEL``
        # autocomplete (the field declares ``datalist: 'openrouter'``). Same shape
        # as ``/api/models`` — ``{'models': [{id, label}]}`` — so the FE fetches it
        # the same way; cached + fallback, so the request never fails the page.
        return jsonify({'models': _discover_openrouter_models(app)})

    @app.get('/api/sessions/<task_id>/model')
    def get_session_model(task_id: str):
        return jsonify({'model': _get_task_override(app, 'TASK_MODEL_OVERRIDES', task_id)})

    @app.post('/api/sessions/<task_id>/model')
    def set_session_model(task_id: str):
        body = request.get_json(silent=True) or {}
        model = text_from_mapping(body, 'model')
        if not _set_task_override(app, 'TASK_MODEL_OVERRIDES', task_id, model):
            return jsonify({'error': 'not available'}), 503
        return jsonify({'model': model})

    # Composer draft (the in-progress prompt: text + pasted images), persisted
    # server-side in <workspace>/.kato-prompts.json so it survives a refresh, a
    # different browser, and switching tasks — not just per-browser localStorage.
    @app.get('/api/sessions/<task_id>/draft')
    def get_session_draft(task_id: str):
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'text': '', 'images': []})
        try:
            workspace_dir = workspace_manager.workspace_path(task_id)
        except Exception:  # noqa: BLE001 - best-effort; unknown task → empty draft
            return jsonify({'text': '', 'images': []})
        return jsonify(read_draft(workspace_dir))

    @app.post('/api/sessions/<task_id>/draft')
    def set_session_draft(task_id: str):
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'error': 'not available'}), 503
        body = request.get_json(silent=True) or {}
        try:
            workspace_dir = workspace_manager.workspace_path(task_id)
        except Exception:  # noqa: BLE001 - best-effort; unknown task is a no-op
            return jsonify({'ok': True})
        write_draft(workspace_dir, body.get('text', ''), body.get('images', []))
        return jsonify({'ok': True})

    @app.get('/api/effort-levels')
    def list_effort_levels():
        # Discovered from the live CLI's --help (not hardcoded), so the
        # set tracks the installed agent version. '' = "Auto" (configured
        # default). ``default`` is the configured effort the UI shows as
        # the Auto resolution.
        return jsonify({
            'levels': _discover_chat_effort_levels(app),
            'default': _configured_chat_effort(app),
        })

    @app.get('/api/sessions/<task_id>/effort')
    def get_session_effort(task_id: str):
        return jsonify({'effort': _get_task_override(app, 'TASK_EFFORT_OVERRIDES', task_id)})

    @app.post('/api/sessions/<task_id>/effort')
    def set_session_effort(task_id: str):
        body = request.get_json(silent=True) or {}
        effort = text_from_mapping(body, 'effort').lower()
        # Guard "not wired" BEFORE validation so a missing store still
        # returns 503 (not a 400) even for an unknown effort.
        if app.config.get('TASK_EFFORT_OVERRIDES') is None:
            return jsonify({'error': 'not available'}), 503
        # '' clears the override (back to Auto). Any explicit level must be
        # one the CLI actually advertises, so a typo can't reach the spawn.
        if effort and effort not in _discover_chat_effort_levels(app):
            return jsonify({
                'error': f'unknown effort {effort!r}; '
                f'expected one of {_discover_chat_effort_levels(app)} or empty',
            }), 400
        _set_task_override(app, 'TASK_EFFORT_OVERRIDES', task_id, effort)
        return jsonify({'effort': effort})

    @app.post('/api/scan/trigger')
    def trigger_scan():
        force_event = app.config.get('FORCE_SCAN_EVENT')
        in_progress = app.config.get('SCAN_IN_PROGRESS_EVENT')
        if force_event is None:
            return jsonify({'status': 'unavailable'}), 503
        if in_progress is not None and in_progress.is_set():
            return jsonify({'status': 'scanning'})
        force_event.set()
        return jsonify({'status': 'triggered'})

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
                sid = read_session_id_from(record)
                if sid and sid not in adopted_by:
                    adopted_by[sid] = record.task_id
        except Exception:  # pragma: no cover — defensive
            adopted_by = {}
        return jsonify({
            'sessions': [
                {
                    **{
                        key: value
                        for key, value in row.to_dict().items()
                        if key != 'agent_session_id'
                    },
                    AGENT_SESSION_ID: row.agent_session_id,
                    'adopted_by_task_id': adopted_by.get(row.agent_session_id, ''),
                }
                for row in rows
            ],
        })

    @app.post('/api/sessions/<task_id>/adopt-agent-session')
    def adopt_agent_session(task_id: str):
        """Bind an existing agent session id to ``task_id``.

        Body: keyed by ``AGENT_SESSION_ID``. The next agent spawn
        for ``task_id`` will ``--resume`` that session instead of
        starting a fresh conversation. Refuses when a live session is
        already running for ``task_id`` — the operator must close it
        first to avoid two writers on the same record.
        """
        payload = request.get_json(silent=True) or {}
        agent_session_id = fix_session_id(payload.get(AGENT_SESSION_ID))
        if not agent_session_id:
            return jsonify({'error': 'agent_session_id is required'}), 400
        manager = app.config['SESSION_MANAGER']
        live_session = manager.get_session(task_id)
        if live_session is not None and live_session.is_alive:
            return jsonify({
                'error': (
                    'a live planning session is already running for this task; '
                    'stop it before adopting a different agent session'
                ),
            }), 409
        try:
            record = manager.adopt_session_id(
                task_id,
                agent_session_id=agent_session_id,
            )
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 409
        migration = _migrate_adopted_session_transcript(
            app, task_id, agent_session_id,
        )
        migration_path = str(migration) if migration else ''
        return jsonify({
            'task_id': record.task_id,
            AGENT_SESSION_ID: record.agent_session_id,
            'transcript_migrated_to': migration_path,
        })

    @app.get('/api/sessions/<task_id>/chats')
    def list_task_chats(task_id: str):
        """List the task's chats — the active one plus detached previous ones.

        Each entry carries transcript metadata (turn count, first/last
        user-message previews, mtime) when the JSONL is on disk, so the
        chats menu can label conversations meaningfully. The active chat
        (if it has a session id yet) comes first; previous chats follow
        newest-detached first.
        """
        manager = app.config['SESSION_MANAGER']
        record = manager.get_record(task_id)
        if record is None:
            return jsonify({'task_id': task_id, 'chats': []})
        active_id = read_session_id_from(record)
        previous_ids = [
            sid for sid in getattr(record, 'previous_session_ids', []) or []
            if sid and sid != active_id
        ]
        ordered = ([active_id] if active_id else []) + list(reversed(previous_ids))
        meta_by_id = _claude_session_metadata_by_id(set(ordered))
        chats = []
        for sid in ordered:
            row = meta_by_id.get(sid)
            chats.append({
                AGENT_SESSION_ID: sid,
                'active': sid == active_id,
                'last_modified_epoch': row.last_modified_epoch if row else 0.0,
                'turn_count': row.turn_count if row else 0,
                'first_user_message': row.first_user_message if row else '',
                'last_user_message': row.last_user_message if row else '',
            })
        return jsonify({'task_id': record.task_id, 'chats': chats})

    @app.post('/api/sessions/<task_id>/chats')
    def start_task_chat(task_id: str):
        """Start a fresh chat, or switch back to one of the task's own chats.

        Empty body (or no id) → detach the current chat and let the next
        message spawn a brand-new Claude session. With an id → it must be
        one of THIS task's chats (current or previous); use the adopt
        endpoint to attach an external session. The live subprocess (if
        any) is terminated as part of the switch.
        """
        payload = request.get_json(silent=True) or {}
        agent_session_id = fix_session_id(payload.get(AGENT_SESSION_ID))
        manager = app.config['SESSION_MANAGER']
        record = manager.get_record(task_id)
        if record is None:
            if agent_session_id:
                return jsonify({
                    'error': f'no session record for task {task_id}',
                }), 404
            # "New chat" on a task that never had a chat: nothing to
            # detach — succeed as a no-op (the first message will spawn
            # fresh anyway) instead of confusing the operator with a 404.
            return jsonify({
                'task_id': task_id,
                AGENT_SESSION_ID: '',
                'previous_session_ids': [],
            })
        # A kato comment-run owns (or is about to own) the session.
        # Switching would kill an IN_PROGRESS fix mid-run — the watcher
        # then requeues it — and a QUEUED comment would be dispatched by
        # the next 2s watcher tick straight INTO the operator's new chat,
        # hijacking the conversation they just asked for. Refuse both.
        # (Note: stopping the session does NOT cancel a comment-run — the
        # watcher respawns it — so the message doesn't suggest that.)
        if _task_has_active_comment_run(app, task_id):
            return jsonify({
                'error': (
                    'kato is working on (or has queued) a review comment '
                    'for this task; wait for it to finish before switching '
                    'chats'
                ),
            }), 409
        if agent_session_id:
            known = {read_session_id_from(record)}
            known.update(getattr(record, 'previous_session_ids', []) or [])
            if agent_session_id not in known:
                return jsonify({
                    'error': (
                        'that session id is not one of this task\'s chats; '
                        'use adopt-agent-session to attach an external session'
                    ),
                }), 400
        try:
            record = manager.start_new_chat(
                task_id, agent_session_id=agent_session_id,
            )
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 404
        return jsonify({
            'task_id': record.task_id,
            AGENT_SESSION_ID: record.agent_session_id,
            'previous_session_ids': list(record.previous_session_ids),
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

    @app.get('/api/settings')
    def get_settings():
        """Operator-editable settings, resolved across all stores.

        Source label tells the operator where the value currently
        lives: ``env`` (live process / shell), ``kato_settings``
        (``~/.kato/settings.json`` — what the UI writes), or
        ``env_file`` (legacy ``<repo>/.env`` fallback).
        """
        from kato_core_lib.helpers.kato_settings_store_utils import (
            kato_settings_path,
        )

        repo_root = _resolve_setting('REPOSITORY_ROOT_PATH')
        return jsonify({
            'repository_root_path': repo_root,
            'settings_file_path': str(kato_settings_path()),
            # Kept for back-compat with any client still reading it.
            'env_file_path': str(_settings_env_path()),
        })

    @app.post('/api/settings')
    def update_settings():
        """Persist the operator-editable settings to ``~/.kato/settings.json``.

        Body: ``{repository_root_path: "/abs/path"}``. The path is
        validated (must exist + be a directory) before the write so
        the operator can't accidentally point kato at a missing
        folder. The write is atomic. The change takes effect on the
        next kato restart (env is read at boot); we say so via
        ``restart_required: true``.
        """
        payload = request.get_json(silent=True) or {}
        new_path = text_from_mapping(payload, 'repository_root_path')
        if not new_path:
            return jsonify({'error': 'repository_root_path is required'}), 400
        # Resolve ``~`` and relative segments so the operator can
        # paste ``~/Projects`` or ``./projects`` and have it land
        # as the canonical absolute path on disk.
        try:
            resolved = Path(new_path).expanduser().resolve()
        except (OSError, ValueError) as exc:
            return jsonify({'error': f'invalid path: {exc}'}), 400
        if not resolved.exists():
            return jsonify({'error': f'path does not exist: {resolved}'}), 400
        if not resolved.is_dir():
            return jsonify({'error': f'path is not a directory: {resolved}'}), 400
        try:
            _persist_settings({'REPOSITORY_ROOT_PATH': str(resolved)})
        except OSError as exc:
            return jsonify({'error': f'failed to write settings file: {exc}'}), 500
        return jsonify({
            'ok': True,
            'repository_root_path': str(resolved),
            'restart_required': True,
            'message': 'Saved. Restart kato for the change to take effect.',
        })

    def _provider_field_values(fields_map):
        """Shared GET shaping for the task / git provider routes.

        Each field resolves across all three stores via
        ``_resolve_setting`` (live env > settings.json > .env).
        Returns ``(out, env_file_values)`` — the second is only used
        by the task route to read the legacy ``KATO_ISSUE_PLATFORM``
        fallback for the "active" label.
        """
        env_file_values = _read_env_file_values(_settings_env_path())
        out = {}
        for name, fields in fields_map.items():
            field_values = {key: _resolve_setting(key) for key in fields}
            out[name] = {'fields': field_values}
        return out, env_file_values

    @app.get('/api/task-providers')
    def list_task_providers():
        """Active task platform + every platform's env-backed fields.

        ``active`` is driven by ``KATO_ISSUE_PLATFORM`` (kato config
        reads ``${oc.env:KATO_ISSUE_PLATFORM,"youtrack"}`` so the env
        var is the operator-facing knob). This is the "where do
        tickets live + which one does kato poll" tab.
        """
        from kato_core_lib.helpers.kato_settings_store_utils import (
            kato_settings_path,
        )

        out, env_file_values = _provider_field_values(_TASK_PROVIDER_FIELDS)
        active = _resolve_setting('KATO_ISSUE_PLATFORM')['value']
        if not active:
            active = env_file_values.get('KATO_ISSUE_PLATFORM', '') or 'youtrack'
        active = active.strip().lower()
        return jsonify({
            'active': active,
            'providers': out,
            'settings_file_path': str(kato_settings_path()),
            'supported': list(_TASK_PROVIDER_FIELDS.keys()),
        })

    @app.post('/api/task-providers')
    def update_task_provider():
        """Patch ``<repo>/.env`` with one task platform's fields + active.

        Body: ``{active?, provider?, fields?}``. ``active`` switches
        ``KATO_ISSUE_PLATFORM``. Only keys in the named provider's
        whitelist are written — a payload can't smuggle unrelated
        env keys. ``restart_required: true`` because kato reads the
        env at boot.
        """
        payload = request.get_json(silent=True) or {}
        updates: dict[str, str] = {}
        active = text_from_mapping(payload, 'active').lower()
        if active:
            if active not in _TASK_PROVIDER_FIELDS:
                return jsonify({
                    'error': f'unknown task provider: {active}. Pick one of '
                             f'{list(_TASK_PROVIDER_FIELDS.keys())}.',
                }), 400
            updates['KATO_ISSUE_PLATFORM'] = active
        provider = text_from_mapping(payload, 'provider').lower()
        fields = payload.get('fields') or {}
        if provider:
            if provider not in _TASK_PROVIDER_FIELDS:
                return jsonify({'error': f'unknown task provider: {provider}'}), 400
            if not isinstance(fields, dict):
                return jsonify({'error': 'fields must be an object'}), 400
            updates.update(
                _filtered_provider_updates(_TASK_PROVIDER_FIELDS, provider, fields),
            )
        if not updates:
            return jsonify({'error': 'no recognised updates'}), 400
        return _validate_persist_and_respond(updates)

    @app.get('/api/git-providers')
    def list_git_providers():
        """Credentials for the git hosts (Bitbucket / GitHub / GitLab).

        NO active selector — kato infers the host from each repo's
        remote URL. This is purely "set the creds kato uses to
        clone / push / open PRs against <host>".
        """
        from kato_core_lib.helpers.kato_settings_store_utils import (
            kato_settings_path,
        )

        out, _ = _provider_field_values(_GIT_HOST_FIELDS)
        return jsonify({
            'providers': out,
            'settings_file_path': str(kato_settings_path()),
            'supported': list(_GIT_HOST_FIELDS.keys()),
        })

    @app.post('/api/git-providers')
    def update_git_provider():
        """Patch ``<repo>/.env`` with one git host's credentials.

        Body: ``{provider, fields}``. Does NOT touch
        ``KATO_ISSUE_PLATFORM`` — selecting a git host here only
        edits its connection creds. Only that host's whitelisted
        keys are written.
        """
        payload = request.get_json(silent=True) or {}
        provider = text_from_mapping(payload, 'provider').lower()
        fields = payload.get('fields') or {}
        if not provider or provider not in _GIT_HOST_FIELDS:
            return jsonify({
                'error': f'unknown git host: {provider or "(none)"}. '
                         f'Pick one of {list(_GIT_HOST_FIELDS.keys())}.',
            }), 400
        if not isinstance(fields, dict):
            return jsonify({'error': 'fields must be an object'}), 400
        updates = _filtered_provider_updates(_GIT_HOST_FIELDS, provider, fields)
        if not updates:
            return jsonify({'error': 'no recognised fields'}), 400
        return _validate_persist_and_respond(updates)

    @app.get('/api/all-settings')
    def list_all_settings():
        """Schema + resolved values for every env-backed setting.

        Powers the schema-driven Settings tabs (General, Claude
        agent, Sandbox, Security scanner, Email & Slack, OpenHands,
        Docker/infra, AWS). Provider/repo-root keys are intentionally
        absent — they have dedicated tabs with custom logic.
        """
        from kato_core_lib.helpers.kato_settings_schema_utils import (
            ACTION_GUARD_SECURE_DEFAULTS,
            schema_for_api,
        )
        from kato_core_lib.helpers.kato_settings_store_utils import (
            kato_settings_path,
        )

        schema = schema_for_api()
        for section in schema:
            for field in section['fields']:
                resolved = _resolve_setting(field['key'])
                field['value'] = resolved['value']
                field['source'] = resolved['source']
                # Action Guard pickers must always show a CONCRETE posture
                # (never blank / "Auto") — fill the secure default when the
                # operator has not set one. The picker then reflects exactly
                # what the guard will enforce.
                if (not str(field['value']).strip()
                        and field['key'] in ACTION_GUARD_SECURE_DEFAULTS):
                    field['value'] = ACTION_GUARD_SECURE_DEFAULTS[field['key']]
                    field['source'] = 'action_guard_secure_default'
        return jsonify({
            'sections': schema,
            'settings_file_path': str(kato_settings_path()),
        })

    @app.post('/api/all-settings')
    def update_all_settings():
        """Persist any schema-declared key to ``~/.kato/settings.json``.

        Body: ``{updates: {KEY: value}}``. The schema is the
        whitelist — a key not declared in any section is dropped, so
        a payload can't smuggle one the UI doesn't own. Booleans /
        numbers are coerced to the string form ``.env`` land
        expects. ``restart_required`` because kato reads env at boot.
        """
        from kato_core_lib.helpers.kato_settings_schema_utils import (
            all_settings_keys,
        )

        payload = request.get_json(silent=True) or {}
        raw = payload.get('updates')
        if not isinstance(raw, dict):
            return jsonify({'error': 'updates must be an object'}), 400
        allowed = all_settings_keys()
        updates: dict[str, str] = {}
        for key, value in raw.items():
            if key not in allowed:
                continue
            if isinstance(value, bool):
                updates[key] = 'true' if value else 'false'
            else:
                updates[key] = str(value if value is not None else '')
        if not updates:
            return jsonify({'error': 'no recognised settings in payload'}), 400
        return _validate_persist_and_respond(updates)

    @app.get('/api/repository-approvals')
    def list_repository_approvals():
        """Return every discovered candidate + which are approved.

        Used by the Settings drawer's "Repositories" approval panel.
        Replaces the ``./kato approve-repo`` CLI picker — discovery
        is the same (inventory + checkout + workspace clones,
        merged inventory-wins). The UI joins each candidate with
        its approval record so the operator sees a single unified
        list with the current mode.
        """
        try:
            from kato_core_lib.data_layers.service.repository_approval_discovery_service import (
                discover_all_repositories,
            )
            from kato_core_lib.data_layers.service.repository_approval_service import (
                RepositoryApprovalService,
            )
        except ImportError as exc:     # pragma: no cover — kato_core_lib is always installed; fallback for embedded webserver use
            return jsonify({'error': f'approvals not available: {exc}'}), 503
        candidates = discover_all_repositories()
        service = RepositoryApprovalService()
        approvals = {
            entry.repository_id.lower(): entry for entry in service.list_approvals()
        }
        out = []
        for repo in candidates:
            entry = approvals.get(repo.repository_id.lower())
            out.append({
                'repository_id': repo.repository_id,
                'remote_url': repo.remote_url,
                'source': repo.source,
                'workspace_path': repo.workspace_path,
                'approved': entry is not None,
                'approval_mode': entry.approval_mode.value if entry else '',
                'approved_remote_url': entry.remote_url if entry else '',
                'approved_by': entry.approved_by if entry else '',
                'remote_url_drift': bool(
                    entry and entry.remote_url and entry.remote_url != repo.remote_url
                ),
            })
        # Also surface "approved but no longer discovered" entries —
        # the operator can still revoke them from the UI even if
        # their workspace clone is gone.
        discovered_ids = {repo.repository_id.lower() for repo in candidates}
        for entry in service.list_approvals():
            if entry.repository_id.lower() in discovered_ids:
                continue
            out.append({
                'repository_id': entry.repository_id,
                'remote_url': entry.remote_url,
                'source': 'orphan',
                'workspace_path': '',
                'approved': True,
                'approval_mode': entry.approval_mode.value,
                'approved_remote_url': entry.remote_url,
                'approved_by': entry.approved_by,
                'remote_url_drift': False,
            })
        out.sort(key=lambda row: row['repository_id'].lower())
        return jsonify({
            'repositories': out,
            'storage_path': str(service.storage_path),
        })

    @app.post('/api/repository-approvals')
    def update_repository_approvals():
        """Apply a batch of approve / revoke / mode-change operations.

        Body shape::

            {
              "approve": [
                {"repository_id": "client", "remote_url": "...", "mode": "trusted"}
              ],
              "revoke": ["other-repo"]
            }

        Empty arrays are tolerated. ``mode`` defaults to ``restricted``.
        Returns the updated list so the UI can re-render without a
        second GET.
        """
        try:
            from kato_core_lib.data_layers.data.repository_approval import (
                ApprovalMode,
            )
            from kato_core_lib.data_layers.service.repository_approval_service import (
                RepositoryApprovalService,
            )
        except ImportError as exc:     # pragma: no cover — kato_core_lib is always installed; fallback for embedded webserver use
            return jsonify({'error': f'approvals not available: {exc}'}), 503
        payload = request.get_json(silent=True) or {}
        approve_in = payload.get('approve') or []
        revoke_in = payload.get('revoke') or []
        if not isinstance(approve_in, list) or not isinstance(revoke_in, list):
            return jsonify({'error': 'approve / revoke must be arrays'}), 400
        service = RepositoryApprovalService()
        applied = {'approved': [], 'revoked': []}
        # Approve first so a rapid toggle (approve → revoke → approve)
        # ends in the expected state when sent in one batch.
        for item in approve_in:
            if not isinstance(item, dict):
                continue
            repo_id = text_from_mapping(item, 'repository_id')
            remote_url = text_from_mapping(item, 'remote_url')
            mode = str(item.get('mode') or 'restricted').strip().lower()
            if not repo_id:
                continue
            try:
                approval_mode = ApprovalMode.from_string(mode)
            except Exception:
                approval_mode = ApprovalMode.RESTRICTED
            entry = service.approve(repo_id, remote_url, mode=approval_mode)
            applied['approved'].append({
                'repository_id': entry.repository_id,
                'mode': entry.approval_mode.value,
            })
        for repo_id in revoke_in:
            repo_id = str(repo_id or '').strip()
            if not repo_id:
                continue
            if service.revoke(repo_id):
                applied['revoked'].append(repo_id)
        return jsonify({
            'ok': True,
            'applied': applied,
        })

    @app.get('/logo.png')
    def logo():
        return _send_kato_png(not_found_message='logo not found')

    @app.get('/favicon.png')
    def favicon_png():
        # Browsers cache favicons aggressively. Tell them to revalidate so
        # a fresh kato.png gets picked up without forcing the operator to
        # clear browser site data.
        return _send_kato_png(
            cache_control='no-cache, must-revalidate',
            not_found_message='favicon not found',
        )

    @app.get('/favicon.ico')
    def favicon_ico():
        # Browsers probe /favicon.ico by default even without a <link>
        # tag. Serve the same PNG (mislabelled as image/x-icon is fine,
        # every browser kato targets honors the actual content).
        return _send_kato_png(
            cache_control='no-cache, must-revalidate',
            not_found_message='favicon not found',
        )

    @app.post('/api/sessions/<task_id>/repositories/<repo_id>/recheck-push')
    def recheck_repository_push(task_id: str, repo_id: str):
        """Re-test push access for a read-only repo (the tree's "try again").

        Returns ``{repo_id, read_only}`` — ``read_only`` is false once kato can
        push (push permission was granted); the UI then reloads the tree.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        recheck = getattr(agent_service, 'recheck_repository_push_access', None)
        if not callable(recheck):
            return jsonify({'error': 'push re-check is not available'}), 503
        try:
            pushable = recheck(task_id, repo_id)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'repo_id': repo_id, 'read_only': not pushable})

    @app.get('/api/sessions/<task_id>/files')
    def list_session_files(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        agent_service = app.config.get('AGENT_SERVICE')
        # Repos kato can't push to (read-only / reference) — badge them in the
        # tree so the operator knows edits there won't be published.
        from kato_core_lib.helpers.read_only_repos_store import read_only_repos
        read_only_ids = read_only_repos(task_id)
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
                    'read_only': repo_id in read_only_ids,
                    'tree': tracked_file_tree(cwd),
                    # Conflict markers — same source as the Changes
                    # tab. UI marks each path with a warning icon so
                    # the operator spots merge conflicts at a glance.
                    'conflicted_files': conflicted_paths(cwd),
                    # Files that differ from the destination branch —
                    # same base + coverage as the Changes-tab diff so
                    # the tree can colour what kato has touched.
                    'changed_files': _changed_files_for_repo(
                        repo_id, cwd, agent_service,
                    ),
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
                'changed_files': [],
            })
        legacy_tree = tracked_file_tree(cwd)
        legacy_conflicts = conflicted_paths(cwd)
        legacy_changed = _changed_files_for_repo('', cwd, agent_service)
        return jsonify({
            'repository_ids': [],
            'trees': [{
                'repo_id': '', 'cwd': cwd, 'tree': legacy_tree,
                'conflicted_files': legacy_conflicts,
                'changed_files': legacy_changed,
            }],
            'cwd': cwd,
            'tree': legacy_tree,
            'conflicted_files': legacy_conflicts,
            'changed_files': legacy_changed,
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

    @app.get('/api/sessions/<task_id>/file')
    def get_session_file(task_id: str):
        """Return the contents of a single tracked file in the task workspace.

        Powers the in-browser Monaco read-only editor: the operator
        clicks a file in the Files tree and the editor loads it here.

        Required query params:
          ``path``  absolute path to the file (as returned by the
                    file-tree endpoint's ``node.data.path``).

        Safety:
          - The path MUST live inside one of the task's workspace
            clones, otherwise we refuse with 403. This guards
            against ``..`` traversal that could leak host files.
          - Files larger than 1MB are refused (Monaco struggles
            past that point and the operator almost never wants
            to read them anyway).
          - Binary content is detected by a NUL-byte scan in the
            first 8KB and returned as ``{ "binary": true }`` rather
            than a string — the UI shows a placeholder.
        """
        path_arg = (request.args.get('path') or '').strip()
        if not path_arg:
            return jsonify({'error': 'path query parameter is required'}), 400
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        # Build the set of legitimate workspace roots for this task
        # so we can refuse anything that escapes them.
        roots: list[str] = []
        for repo_id in _task_repository_ids(workspace_manager, task_id):
            cwd = _repository_cwd(workspace_manager, task_id, repo_id)
            if cwd:
                roots.append(cwd)
        if not roots:
            manager = app.config['SESSION_MANAGER']
            legacy_cwd = _record_cwd_or_none(manager, task_id)
            if legacy_cwd:
                roots.append(legacy_cwd)
        if not roots:
            return jsonify({'error': 'no workspace for this task'}), 404
        from pathlib import Path
        # The file tree returns repo-relative paths (e.g.
        # ``dev_scripts/export_users.py``) — the UI forwards those
        # verbatim. An absolute path is also accepted so legacy
        # callers / direct API users keep working. For a relative
        # input we try joining with each workspace root and pick the
        # first one that lands on a real file inside that root.
        candidates: list[Path] = []
        raw_path = Path(path_arg)
        if raw_path.is_absolute():
            try:
                candidates.append(raw_path.resolve())
            except (OSError, ValueError):
                return jsonify({'error': 'invalid path'}), 400
        else:
            for root in roots:
                try:
                    candidates.append((Path(root) / raw_path).resolve())
                except (OSError, ValueError):
                    continue
        resolved_roots: list[Path] = []
        for root in roots:
            try:
                resolved_roots.append(Path(root).resolve())
            except (OSError, ValueError):
                continue
        # First preference: a candidate that lives inside a root AND
        # exists on disk — the file the operator actually clicked.
        # Fallback: a candidate that lives inside a root but doesn't
        # exist (so the caller still gets a clear 404 instead of a
        # 403). 403 is reserved for "path escaped every root".
        resolved: Path | None = None
        in_workspace: Path | None = None
        for candidate in candidates:
            inside_a_root = any(
                _is_inside(candidate, root_resolved)
                for root_resolved in resolved_roots
            )
            if not inside_a_root:
                continue
            if in_workspace is None:
                in_workspace = candidate
            if candidate.is_file():
                resolved = candidate
                break
        if resolved is None and in_workspace is None:
            return jsonify({'error': 'path is outside the task workspace'}), 403
        if resolved is None:
            return jsonify({'error': 'file not found'}), 404
        if not resolved.is_file():
            return jsonify({'error': 'file not found'}), 404
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            return jsonify({'error': f'stat failed: {exc}'}), 500
        # 1 MB cap — Monaco's perf cliff is around 5MB but file
        # diffs that big are pathological and rarely useful for a
        # read-only preview.
        if size > 1_000_000:
            return jsonify({
                'error': 'file too large for preview (max 1 MB)',
                'size': size,
                'too_large': True,
            }), 200
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            return jsonify({'error': f'read failed: {exc}'}), 500
        if b'\x00' in raw[:8192]:
            return jsonify({
                'path': str(resolved),
                'size': size,
                'binary': True,
            })
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('utf-8', errors='replace')
        return jsonify({
            'path': str(resolved),
            'size': size,
            'binary': False,
            'content': content,
        })

    @app.get('/api/sessions/<task_id>/base-file')
    def get_session_base_file(task_id: str):
        """Return a file as it exists at the configured diff base."""
        path_arg = (request.args.get('path') or '').strip()
        repo_id = (request.args.get('repo') or '').strip()
        if not path_arg:
            return jsonify({'error': 'path query parameter is required'}), 400
        if path_arg == '/dev/null':
            return jsonify({'error': 'file not found at base'}), 404
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        manager = app.config['SESSION_MANAGER']
        agent_service = app.config.get('AGENT_SERVICE')
        cwd = (
            _repository_cwd(workspace_manager, task_id, repo_id)
            if repo_id
            else _record_cwd_or_none(manager, task_id)
        )
        if cwd is None:
            return jsonify({'error': 'no workspace for this task'}), 404
        rel_path = _repo_relative_path(path_arg, cwd)
        if rel_path is None:
            return jsonify({'error': 'path is outside the task repository'}), 403
        base = _resolve_diff_base(repo_id, cwd, agent_service)
        if not base:
            return jsonify({'error': _no_base_error_message(repo_id)}), 404
        ref = f'origin/{base}'
        size = blob_size_at_ref(cwd, ref, rel_path)
        if size is None:
            return jsonify({'error': 'file not found at base'}), 404
        if size > 1_000_000:
            return jsonify({
                'error': 'file too large for context expansion (max 1 MB)',
                'size': size,
                'too_large': True,
            }), 200
        content = file_text_at_ref(cwd, ref, rel_path)
        if content is None:
            return jsonify({'error': 'file not found at base'}), 404
        return _file_preview_payload(
            size, content,
            {'repo_id': repo_id, 'path': rel_path, 'base': base},
        )

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
        approve, err = _resolve_agent_method(
            app, 'approve_push',
            not_callable_message='agent service does not support push approval',
        )
        if err:
            return err
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
        push, err = _resolve_agent_method(
            app, 'push_task',
            not_callable_message='agent service does not support push',
        )
        if err:
            return err
        return _envelope_response(push(task_id), 'pushed')

    @app.post('/api/sessions/<task_id>/pull')
    def pull_task(task_id: str):
        """Operator-triggered fast-forward pull from the planning UI's
        ``Pull`` button. Symmetric to ``/push``."""
        pull, err = _resolve_agent_method(
            app, 'pull_task',
            not_callable_message='agent service does not support pull',
        )
        if err:
            return err
        return _envelope_response(pull(task_id), 'pulled')

    @app.post('/api/sessions/<task_id>/merge-default-branch')
    def merge_default_branch(task_id: str):
        """Fetch + merge each clone's default branch into the task branch.

        Drives the planning UI's ``Merge master`` button. On
        conflict the markers are left in the working tree (not
        aborted) so the chat agent can resolve them — the clone is
        intentionally blocked from running git itself.
        """
        merge, err = _resolve_agent_method(
            app, 'merge_default_branch_for_task',
            not_callable_message='agent service does not support merge-default',
        )
        if err:
            return err
        result = merge(task_id) or {}
        # A conflicted merge is a SUCCESSFUL outcome of this button —
        # the operator wanted the default branch in so the agent can
        # fix conflicts. Only a hard error (no workspace / git
        # failure) is non-2xx.
        err = result.get('error')
        if err and not result.get('merged') and not result.get('has_conflicts'):
            return jsonify(result), 404 if 'no workspace' in str(err) else 500
        return jsonify(result)

    @app.post('/api/sessions/<task_id>/pull-request')
    def create_task_pull_request(task_id: str):
        """Operator-triggered PR open from the planning UI's ``Pull request`` button."""
        create, err = _resolve_agent_method(
            app, 'create_pull_request_for_task',
            not_callable_message='agent service does not support PR creation',
        )
        if err:
            return err
        return _envelope_response(create(task_id), 'created')

    @app.post('/api/sessions/<task_id>/update-source')
    def update_task_source(task_id: str):
        """Push + sync the operator's REPOSITORY_ROOT_PATH clones to the
        task branch. Pure git plumbing — no AI involvement. Drives the
        planning UI's ``Update source`` button.
        """
        update, err = _resolve_agent_method(
            app, 'update_source_for_task',
            not_callable_message='agent service does not support source-update',
        )
        if err:
            return err
        return _envelope_response(update(task_id), 'updated')

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
        add_comment, err = _resolve_agent_method(
            app, 'add_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        result = add_comment(
            task_id,
            repo_id=text_from_mapping(payload, 'repo'),
            file_path=text_from_mapping(payload, 'file_path'),
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
        resolve, err = _resolve_agent_method(
            app, 'resolve_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
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
        mark, err = _resolve_agent_method(
            app, 'mark_comment_addressed',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        return jsonify(mark(
            task_id, comment_id,
            addressed_sha=str(payload.get('addressed_sha') or ''),
        ))

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/reopen')
    def reopen_task_comment(task_id: str, comment_id: str):
        reopen, err = _resolve_agent_method(
            app, 'reopen_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        return jsonify(reopen(task_id, comment_id))

    @app.delete('/api/sessions/<task_id>/comments/<comment_id>')
    def delete_task_comment(task_id: str, comment_id: str):
        delete, err = _resolve_agent_method(
            app, 'delete_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        return jsonify(delete(task_id, comment_id))

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/edit')
    def edit_task_comment(task_id: str, comment_id: str):
        """Edit a queued local comment's body and/or kato_status.

        Body (JSON): ``{"body": "...", "kato_status": "editing"|"queued"}``
        — both optional. Used by the inline-edit flow: the UI flips a
        QUEUED comment to ``editing`` when the operator opens the
        editor (so the agent skips it), then back to ``queued`` with
        the new body on save (or just back to ``queued`` on cancel).
        """
        edit, err = _resolve_agent_method(
            app, 'edit_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        body = payload.get('body')
        kato_status = payload.get('kato_status')
        return jsonify(edit(
            task_id, comment_id,
            body=None if body is None else str(body),
            kato_status=None if kato_status is None else str(kato_status),
        ))

    @app.post('/api/sessions/<task_id>/comments/sync')
    def sync_task_comments(task_id: str):
        """Pull remote PR comments + ``git pull`` the workspace clone."""
        sync, err = _resolve_agent_method(
            app, 'sync_remote_comments',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        repo_id = text_from_mapping(payload, 'repo')
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
        adopt, err = _resolve_agent_method(app, 'adopt_task')
        if err:
            return err
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
        add_repo, err = _resolve_agent_method(
            app, 'add_task_repository',
            not_callable_message='agent service does not support add-repository',
        )
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        repository_id = text_from_mapping(payload, 'repository_id')
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
        sync, err = _resolve_agent_method(
            app, 'sync_task_repositories',
            not_callable_message='agent service does not support repo sync',
        )
        if err:
            return err
        return _envelope_response(sync(task_id), 'synced')

    @app.post('/api/sessions/<task_id>/finish')
    def finish_task(task_id: str):
        """Operator-triggered "I'm done" — same flow Claude triggers via
        the ``<KATO_TASK_DONE>`` sentinel. Pushes pending changes, opens
        a PR if none exists, and moves the ticket to In Review.
        """
        finish, err = _resolve_agent_method(
            app, 'finish_task_planning_session',
            not_callable_message='agent service does not support finish',
        )
        if err:
            return err
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

    @app.get('/api/sessions/<task_id>/search')
    def search_task_workspace_content(task_id: str):
        """Content (grep) search across the task's workspace repos."""
        query = str(request.args.get('q', '') or '').strip()
        agent_service = app.config.get('AGENT_SERVICE')
        search = getattr(agent_service, 'search_task_workspace', None)
        if not query or not callable(search):
            return jsonify({'matches': [], 'truncated': False, 'query': query})
        try:
            return jsonify(search(task_id, query))
        except Exception as exc:
            return jsonify({'error': str(exc), 'matches': [], 'query': query}), 500

    @app.delete('/api/sessions/<task_id>/workspace')
    def forget_task_workspace(task_id: str):
        """Manual escape hatch: wipe everything for ``task_id``.

        Operator-reported regression: clicking the tab × showed a
        red dot, the tab stayed in the strip, and ``~/.kato/
        workspaces/<task_id>/`` was still on disk. Causes:
          1. The live Claude subprocess wasn't terminated, so its
             open file handles in the clone blocked ``shutil.rmtree``
             on Windows (file-lock semantics).
          2. The session record (``~/.kato/sessions/<task_id>.json``)
             was never removed, so the tab kept reappearing on the
             next ``/api/sessions`` poll.
          3. ``workspace_manager.delete()`` swallows ``OSError``
             internally — the rmtree failure was logged at warning
             level but never surfaced to the UI.

        Fix order: terminate session FIRST (kills subprocess +
        removes record + deletes Claude transcript), THEN delete
        the workspace clone, THEN VERIFY the directory is actually
        gone before returning 200. If anything's left, the operator
        gets a concrete error message they can act on.
        """
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'error': 'workspace manager not wired'}), 503
        errors: list[str] = []
        # 1. Kill the live subprocess + wipe the session record /
        #    Claude JSONL transcript. Best-effort: a missing session
        #    or terminate failure shouldn't block the workspace
        #    cleanup that's right after.
        session_manager = app.config.get('SESSION_MANAGER')
        if session_manager is not None:
            try:
                session_manager.terminate_session(
                    task_id, remove_record=True,
                )
            except Exception as exc:
                errors.append(f'terminate_session: {exc}')
        # Record the operator's intent FIRST so the platform poll (the
        # review-comment scan, which discovers in-review tasks from YouTrack/
        # Bitbucket — not from local records) doesn't resurrect this task on the
        # next tick or after a restart, even if the clone delete below partially
        # fails on a file lock. Cleared when the operator re-adopts the task.
        from kato_core_lib.helpers.forgotten_tasks_store import forget as _mark_forgotten
        _mark_forgotten(task_id)
        # 2. Wipe the per-task workspace clone(s). ``delete``
        #    silently swallows ``OSError``; we VERIFY after.
        try:
            workspace_manager.delete(task_id)
        except Exception as exc:
            errors.append(f'workspace.delete: {exc}')
        # 3. Verify nothing's left behind. The only reason a
        #    well-formed delete would leave the dir is a file lock
        #    (Windows antivirus, another process with handles open,
        #    a clone with read-only files). Surfacing this to the
        #    UI is the operator's escape hatch.
        try:
            workspace_dir = workspace_manager.workspace_path(task_id)
        except Exception:
            workspace_dir = None
        if workspace_dir is not None and workspace_dir.exists():
            errors.append(
                f'workspace directory still exists at {workspace_dir} '
                '(likely a file lock — close any process with files '
                'open in this clone and try again)'
            )
        if errors:
            return jsonify({
                'forgotten': False,
                'task_id': task_id,
                'error': '; '.join(errors),
            }), 500
        return jsonify({'forgotten': True, 'task_id': task_id})


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
            return _sse_response(_empty(), accel=False)
        return _sse_response(_status_event_stream(broadcaster))


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
    _register_get_pending_permissions_route(app)
    _register_action_guard_audit_route(app)
    _register_agent_version_route(app)


def _register_session_events_route(app: Flask) -> None:
    @app.get('/api/sessions/<task_id>/events')
    def session_events_stream(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        agent_service = app.config.get('AGENT_SERVICE')
        return _sse_response(
            _event_stream_generator(
                manager, workspace_manager, task_id, agent_service,
            ),
        )


def _chat_runner_defaults(app: Flask):
    """Return the planning runner's ``_defaults`` (binary, effort, …) or None."""
    runner = app.config.get('PLANNING_SESSION_RUNNER')
    return getattr(runner, '_defaults', None) if runner is not None else None


# Concrete effort kato falls back to when neither a per-task override nor a
# configured default is set. The composer used to show "Auto" here, which hid
# the real effort — kato passed no --effort, so the CLI silently picked one and
# the operator couldn't tell which. Surfacing a concrete level (and passing it
# explicitly on spawn) means the effort actually used is always visible.
DEFAULT_CHAT_EFFORT = 'high'


def _configured_chat_effort(app: Flask) -> str:
    defaults = _chat_runner_defaults(app)
    configured = (
        str(getattr(defaults, 'effort', '') or '') if defaults is not None else ''
    )
    return configured or DEFAULT_CHAT_EFFORT


def _discover_chat_effort_levels(app: Flask) -> list:
    """Effort levels the chat CLI advertises (discovered, with fallback)."""
    defaults = _chat_runner_defaults(app)
    binary = str(getattr(defaults, 'binary', '') or 'claude') if defaults else 'claude'
    try:
        from claude_core_lib.claude_core_lib.helpers.effort_levels import (
            discover_effort_levels,
        )
        return discover_effort_levels(binary)
    except Exception:
        app.logger.exception('effort-level discovery failed; using fallback')
        from claude_core_lib.claude_core_lib.helpers.effort_levels import (
            FALLBACK_EFFORT_LEVELS,
        )
        return list(FALLBACK_EFFORT_LEVELS)


def _discover_chat_models(app: Flask) -> list:
    """Models the chat backend offers (discovered, with fallback).

    Never hardcodes a version. Claude serves the stable CLI aliases
    (opus/sonnet/haiku) — which always resolve to the latest — with labels
    enriched by the live Anthropic models API when a credential is configured;
    Codex reads the codex CLI's own model cache. Any failure falls back to a
    sane static set so the picker always renders.
    """
    defaults = _chat_runner_defaults(app)
    binary = str(getattr(defaults, 'binary', '') or 'claude') if defaults else 'claude'
    try:
        if 'codex' in binary.lower():
            from codex_core_lib.codex_core_lib.helpers.model_discovery import (
                discover_codex_models,
            )
            models = discover_codex_models()
        else:
            from claude_core_lib.claude_core_lib.helpers.model_catalog import (
                discover_models,
            )
            models = discover_models()
    except Exception:
        app.logger.exception('model discovery failed; using fallback')
        from claude_core_lib.claude_core_lib.helpers.model_catalog import (
            FALLBACK_MODELS,
        )
        models = [dict(model) for model in FALLBACK_MODELS]
    return _apply_configured_default(app, models)


def _configured_chat_model(app: Flask) -> str:
    """The model the chat runner falls back to when a task has no override.

    This is ``runner._defaults.model`` — i.e. what spawn uses as ``model or
    self._defaults.model``. Empty string means "no configured model", in which
    case the CLI picks its own default (which ``discover`` already flags).
    """
    defaults = _chat_runner_defaults(app)
    return str(getattr(defaults, 'model', '') or '').strip() if defaults else ''


def _apply_configured_default(app: Flask, models: list) -> list:
    """Re-point the ``default`` flag onto the model the runner actually falls back to.

    The composer shows the default-flagged model (instead of an ambiguous
    "Default" entry) when a task has no per-task override, so that flag must
    name the model spawn would really use: ``runner._defaults.model`` if
    configured, otherwise the CLI's own default (whatever discovery already
    flagged). A configured value that matches NO offered id is surfaced as
    its own flagged entry — spawn passes it verbatim, so leaving the
    discovery flag (e.g. sonnet) in place would make the picker claim a
    model that will not run (the no-ambiguous-picker rule).
    """
    configured = _configured_chat_model(app)
    if not configured:
        return models
    target = _match_model_alias(configured, [m.get('id') for m in models])
    adjusted = []
    for model in models:
        model = dict(model)
        model.pop('default', None)
        if target and model.get('id') == target:
            model['default'] = True
        adjusted.append(model)
    if not target:
        adjusted.append({
            'id': configured,
            'label': f'{configured} (configured)',
            'default': True,
        })
    return adjusted


def _match_model_alias(configured: str, ids: list) -> str:
    """Map a configured model value to one of the offered option ids, or ''.

    Matches exactly first (``opus`` → ``opus``; a codex slug → itself), then by
    Claude family so a full id like ``claude-opus-4-8`` still resolves to the
    ``opus`` alias the picker offers (the alias genuinely runs the latest, so
    family-level matching is truthful there). Fable is stricter — see
    ``_match_pinned_fable_id``.
    """
    candidate = (configured or '').strip().lower()
    if candidate in ids:
        return candidate
    for family in ('opus', 'sonnet', 'haiku'):
        if family in ids and (candidate == family or candidate.startswith(f'claude-{family}')):
            return family
    return _match_pinned_fable_id(candidate, ids)


def _match_pinned_fable_id(candidate: str, ids: list) -> str:
    """Match a configured fable id to the offered pinned id — same version only.

    Fable has no CLI alias: the picker offers a pinned FULL id and spawn
    passes the configured value verbatim, so a family-level fallback could
    flag a DIFFERENT concrete model than the one that will actually run
    (e.g. configured ``claude-fable-5`` highlighted as an offered
    ``claude-fable-6``). Only suffix variants of the SAME version match
    (``claude-fable-5[1m]`` → ``claude-fable-5``). The bare word ``fable``
    matches nothing — the CLI rejects ``--model fable``, and mapping it
    would legitimize a config that fails at spawn.
    """
    from claude_core_lib.claude_core_lib.helpers.model_catalog import (
        family_version_from_model_id,
    )
    wanted = family_version_from_model_id(candidate)
    if wanted is None or wanted[0] != 'fable':
        return ''
    for offered in ids:
        if not isinstance(offered, str):
            continue
        parsed = family_version_from_model_id(offered)
        if parsed is not None and parsed[:3] == wanted[:3]:
            return offered
    return ''


def _discover_openrouter_models(app: Flask) -> list:
    """OpenRouter models for the settings autocomplete (discovered, with fallback).

    Mirrors ``_discover_chat_models``: returns ``[{id, label}]`` from the live
    public catalogue (cached), and an empty list on any failure so the settings
    page always renders.
    """
    try:
        from kato_core_lib.helpers.openrouter_model_discovery import (
            discover_openrouter_models,
        )
        return discover_openrouter_models()
    except Exception:
        app.logger.exception('openrouter model discovery failed')
        return []


def _register_post_message_route(app: Flask) -> None:
    @app.post('/api/sessions/<task_id>/messages')
    def post_message(task_id: str):
        payload = request.get_json(silent=True) or {}
        text = text_from_mapping(payload, 'text')
        images = payload.get('images') or []
        if not isinstance(images, list):
            images = []
        if not text and not images:
            return jsonify({'error': 'text or images is required'}), 400
        _capture_prompt_lesson_candidate(app, task_id, text)
        manager = app.config['SESSION_MANAGER']
        # The CLI bakes ``--model`` and ``--effort`` at spawn, so a
        # changed model / effort only takes hold on a fresh subprocess.
        # If the operator switched to a new explicit value and the live
        # session is idle, respawn it (via ``--resume``, conversation
        # preserved) at the new value instead of forwarding the message
        # into a subprocess that's still on the OLD value. The model
        # check is critical: without it, an operator who changes the
        # picker after a session was spawned with a model they can't
        # access (or that's broken) hits an immediate error on every
        # message until kato restarts.
        if (
            _model_change_needs_respawn(app, manager, task_id, images)
            or _effort_change_needs_respawn(app, manager, task_id, images)
        ):
            try:
                manager.terminate_session(task_id, remove_record=False)
            except Exception:
                app.logger.exception(
                    'failed to terminate session for model/effort respawn (task %s)',
                    task_id,
                )
            return _spawn_or_reject_chat_session(app, task_id, text)
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


def _capture_prompt_lesson_candidate(app: Flask, task_id: str, text: str) -> None:
    """Best-effort early lesson candidate capture for chat prompts."""
    service = app.config.get('AGENT_SERVICE')
    capture = getattr(service, 'capture_prompt_lesson_candidate', None)
    if not callable(capture):
        return
    try:
        capture(task_id, text)
    except Exception:
        app.logger.exception(
            'failed to capture prompt lesson candidate for task %s',
            task_id,
        )


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
        # Fire the ``stop`` hook AFTER the manager kill succeeds so
        # observers (audit log, slack mirror) only see stops that
        # actually went through. The runner isolates its own
        # failures — a misbehaving hook can't 500 this route.
        _fire_webserver_hook(app, 'stop', {
            'task_id': task_id,
            'source': 'webserver_stop_route',
        })
        return jsonify({'status': 'stopped'})


def _fire_webserver_hook(app: Flask, point: str, event: dict) -> None:
    """Fire a configured hook from a webserver route.

    Routes don't import :mod:`kato_core_lib.hooks` directly so the
    webserver can boot without that package installed (test
    environments, embedded use). Lazy-import + isolate failures.
    """
    runner = app.config.get('HOOK_RUNNER')
    if runner is None:
        return
    try:
        from kato_core_lib.hooks.config import HookPoint
        runner.fire(HookPoint(point), dict(event))
    except Exception:
        app.logger.exception('webserver hook firing failed for %s', point)


def _register_post_permission_route(app: Flask) -> None:
    @app.post('/api/sessions/<task_id>/permission')
    def post_permission(task_id: str):
        session, error = _resolve_writable_session(
            app.config['SESSION_MANAGER'], task_id,
        )
        if error is not None:
            return error
        payload = request.get_json(silent=True) or {}
        request_id = text_from_mapping(payload, 'request_id')
        if not request_id:
            return jsonify({'error': 'request_id is required'}), 400
        allow = bool(payload.get('allow', False))
        rationale = str(payload.get('rationale', '') or '')
        # Re-derive the tool SERVER-SIDE (never trust the client body for the
        # command) and classify it. The Action Guard + the operator's
        # pre_tool_use hook only matter when the operator is letting the tool
        # RUN — a deny already stops it. A guard BLOCK force-flips allow→deny.
        tool_name, tool_input = _pending_tool(session, request_id)
        guard_command = str(tool_input.get('command') or '')
        verdict = _classify_action_for(session, tool_name, tool_input)
        guard_blocked = (
            verdict is not None
            and _action_guard_enum_value(verdict.decision) == 'block'
        )
        if allow and guard_blocked:
            allow = False
            rationale = verdict.reason or rationale or 'blocked by Action Guard'
        elif allow:
            blocked, hook_rationale = _run_pre_tool_use_hook(app, task_id, payload)
            if blocked:
                allow = False
                rationale = hook_rationale or rationale or 'blocked by pre_tool_use hook'
        try:
            session.send_permission_response(
                request_id=request_id,
                allow=allow,
                rationale=rationale,
            )
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        # A hard BLOCK becomes a loud bubble in the feed; every risky decision
        # (block / approved-ask / denied-ask) is recorded to the audit log.
        if guard_blocked:
            _publish_action_guard_block(session, verdict)
        _audit_action_guard(app, task_id, request_id, verdict, guard_command, allow)
        # ``post_tool_use`` sees the final, post-hook decision so the
        # audit log reflects what actually got delivered to Claude.
        _fire_webserver_hook(app, 'post_tool_use', {
            'task_id': task_id,
            'request_id': request_id,
            'allow': bool(allow),
            'rationale': rationale,
            'tool': str(payload.get('tool', '') or ''),
            'action_guard_category': (
                _action_guard_enum_value(verdict.category) if verdict else ''
            ),
        })
        return jsonify({'status': 'delivered', 'allow': allow})


def _register_action_guard_audit_route(app: Flask) -> None:
    @app.get('/api/action-guard/audit')
    def get_action_guard_audit():
        """Recent Action Guard decisions (newest first) + chain-verify status.

        Read-only history for the Action Guard settings tab. Best-effort: if
        the audit package is unavailable, return an empty (valid) feed rather
        than error. ``ok=false`` + ``first_bad_index`` flags a tampered log.
        """
        try:
            from kato_core_lib.helpers.action_guard_audit import (
                read_action_guard_audit,
                verify_action_guard_audit,
            )
        except Exception:
            return jsonify({'entries': [], 'ok': True, 'first_bad_index': -1})
        try:
            limit = int(request.args.get('limit', 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))
        entries = list(reversed(read_action_guard_audit(limit=limit)))
        ok, first_bad = verify_action_guard_audit()
        return jsonify({
            'entries': entries,
            'ok': bool(ok),
            'first_bad_index': int(first_bad),
        })


def _register_agent_version_route(app: Flask) -> None:
    @app.get('/api/agent-version')
    def get_agent_version():
        """Configured agent CLI version + capability flags (cached).

        Powers the "agent CLI out of date" banner and hides features the
        installed CLI can't run (e.g. the ultracode/workflow toggle). The
        version doesn't change while kato runs, so it's probed once and cached
        — operators restart kato after upgrading the CLI.
        """
        cached = app.config.get('AGENT_VERSION_INFO')
        if cached is None:
            try:
                from kato_core_lib.helpers.agent_version_utils import (
                    agent_version_info,
                )
                cached = agent_version_info()
            except Exception:
                app.logger.exception('agent version probe failed')
                cached = {
                    'backend': 'unknown', 'binary': '', 'found': True,
                    'version': None, 'version_raw': '', 'recommended_min': '',
                    'up_to_date': True, 'supports_workflows': False, 'detail': '',
                }
            app.config['AGENT_VERSION_INFO'] = cached
        return jsonify(cached)


def _register_get_pending_permissions_route(app: Flask) -> None:
    @app.get('/api/permissions/pending')
    def get_pending_permissions():
        """Every unanswered permission ask across ALL live sessions.

        The per-task SSE stream delivers a ``control_request`` only to the
        browser tab that has that session open, so a permission ask on a
        backgrounded task would otherwise wait until the operator clicked
        into it. The UI polls this so the modal pops no matter which task is
        in focus, each envelope tagged with the ``task_id`` it belongs to (the
        UI titles the modal with it). Best-effort: a session that can't report
        its pending asks is skipped, never fails the whole feed.
        """
        manager = app.config['SESSION_MANAGER']
        pending: list[dict] = []
        for record, session in _iter_live_sessions(manager):
            probe = getattr(session, 'pending_control_requests', None)
            if not callable(probe):
                continue
            try:
                envelopes = probe() or []
            except Exception:
                continue
            for envelope in envelopes:
                if not isinstance(envelope, dict):
                    continue
                envelope = dict(envelope)
                _annotate_action_guard(envelope, session)
                envelope['task_id'] = record.task_id
                # Stamp the task summary alongside the id so the
                # cross-task permission modal can render the full
                # "<TASK-ID> — <summary>" title without a second
                # round-trip; the focused-task path gets the same
                # value from its SessionDetail prop.
                envelope['task_summary'] = str(
                    getattr(record, 'task_summary', '') or '',
                )
                pending.append(envelope)
        return jsonify({'pending': pending})


def _run_pre_tool_use_hook(app: Flask, task_id: str, payload: dict):
    """Fire ``pre_tool_use`` and translate the result into (blocked, rationale).

    Returns ``(False, '')`` when nothing is configured / no runner
    available, so the default path through the permission route is
    unchanged. ``(True, '<reason>')`` when the operator's hook
    explicitly blocks — the route flips allow→deny and uses the
    rationale (or the hook's stderr) in the response to Claude.
    """
    runner = app.config.get('HOOK_RUNNER')
    if runner is None:
        return False, ''
    try:
        from kato_core_lib.hooks.config import HookPoint
        results = runner.fire(HookPoint('pre_tool_use'), {
            'task_id': task_id,
            'request_id': str(payload.get('request_id', '') or ''),
            'tool': str(payload.get('tool', '') or ''),
            'allow': bool(payload.get('allow', False)),
        })
    except Exception:
        app.logger.exception('pre_tool_use hook fire failed')
        return False, ''
    if not results:
        return False, ''
    if runner.is_blocked(results):
        # Surface the first non-empty stderr/error as the rationale
        # so the operator's reason for blocking shows up in the
        # permission response Claude sees.
        rationale = ''
        for result in results:
            if result.blocked:
                rationale = (result.stderr or result.error or '').strip()
                if rationale:
                    break
        return True, rationale
    return False, ''


# --------------------------------------------------------------------------
# Action Guard (Layer B) — content-aware enforcement in the permission path.
#
# Resolved LIVE per decision (no kato restart) and kept entirely on the
# kato/webserver side so claude_core_lib carries zero action-guard logic.
# Everything below is best-effort and FAILS OPEN: a classifier/import error
# never breaks the permission pipeline — Layer A (the CLI denylist floor)
# and Docker containment remain the structural backstop.
# --------------------------------------------------------------------------
def _action_guard_enum_value(value) -> str:
    return str(getattr(value, 'value', value) or '')


def _session_additional_dirs(session) -> tuple:
    getter = getattr(session, 'allowed_additional_dirs', None)
    if not callable(getter):
        return ()
    try:
        return tuple(getter() or ())
    except Exception:
        return ()


def _pending_tool(session, request_id: str):
    """``(tool_name, tool_input)`` for a pending request, server-side only."""
    getter = getattr(session, 'pending_request_input', None)
    if not callable(getter):
        return '', {}
    try:
        tool_name, tool_input = getter(request_id)
        return tool_name, (tool_input if isinstance(tool_input, dict) else {})
    except Exception:
        return '', {}


def _classify_action_for(session, tool_name: str, tool_input: dict):
    """Run the Action Guard engine for one tool call. Returns a GuardVerdict
    or ``None`` (guard unavailable / disabled / error → fail open)."""
    if not tool_name and not tool_input:
        return None
    try:
        from kato_core_lib.helpers.action_guard_config import (
            resolve_action_guard_policy,
        )
        from agent_core_lib.agent_core_lib.helpers.command_policy import (
            classify_action,
        )
        from claude_core_lib.claude_core_lib.helpers.sandbox_scope import (
            classify_command_sandbox,
            classify_tool_input_sandbox,
        )
        return classify_action(
            tool_name, tool_input,
            cwd=str(getattr(session, 'cwd', '') or ''),
            additional_dirs=_session_additional_dirs(session),
            allowed_paths=tuple(getattr(session, 'sandbox_allowed_paths', ()) or ()),
            policy=resolve_action_guard_policy(),
            command_sandbox_classifier=classify_command_sandbox,
            tool_input_sandbox_classifier=classify_tool_input_sandbox,
        )
    except Exception:
        return None


def _annotate_action_guard(raw: dict, session) -> None:
    """Annotate a ``control_request`` raw dict in place with an
    ``action_guard`` block so the permission modal can show the risk. Only
    BLOCK/ASK verdicts annotate; ALLOW/NONE leave the dict untouched."""
    try:
        if not isinstance(raw, dict) or raw.get('type') != CLAUDE_EVENT_CONTROL_REQUEST:
            return
        request = raw.get('request') if isinstance(raw.get('request'), dict) else {}
        tool_name = str(request.get('tool_name') or request.get('tool') or '')
        tool_input = request.get('input') if isinstance(request.get('input'), dict) else {}
        verdict = _classify_action_for(session, tool_name, tool_input)
        if verdict is None:
            return
        decision = _action_guard_enum_value(verdict.decision)
        if decision == 'allow' or _action_guard_enum_value(verdict.category) == 'none':
            return
        raw['action_guard'] = {
            'category': _action_guard_enum_value(verdict.category),
            'decision': decision,
            'reason': verdict.reason,
            'rule_id': verdict.rule_id,
        }
    except Exception:
        pass


def _publish_action_guard_block(session, verdict) -> None:
    """Surface a hard BLOCK as a loud system bubble in the session feed."""
    publisher = getattr(session, 'publish_system_notice', None)
    if not callable(publisher):
        return
    category = _action_guard_enum_value(verdict.category)
    reason = verdict.reason or 'blocked by Action Guard'
    try:
        publisher(
            CLAUDE_SYSTEM_SUBTYPE_ACTION_GUARD_BLOCK,
            f'BLOCKED by Action Guard ({category}): {reason}. The agent was '
            'refused this action and told why.',
            {'action_guard': {
                'category': category,
                'decision': 'block',
                'reason': reason,
                'rule_id': verdict.rule_id,
            }},
        )
    except Exception:
        pass


def _audit_action_guard(app, task_id, request_id, verdict, command, allow) -> None:
    """Record the decision to the hash-chained audit log (best-effort)."""
    if verdict is None or _action_guard_enum_value(verdict.category) == 'none':
        return
    if _action_guard_enum_value(verdict.decision) == 'block':
        decision_label = 'block'
    else:
        decision_label = 'ask_approved' if allow else 'ask_denied'
    try:
        from kato_core_lib.helpers.action_guard_audit import (
            record_action_guard_decision,
        )
        record_action_guard_decision(
            task_id=task_id,
            category=_action_guard_enum_value(verdict.category),
            decision=decision_label,
            command=command,
            rule_id=verdict.rule_id,
            request_id=request_id,
            answered_by=os.environ.get('KATO_OPERATOR_EMAIL', ''),
        )
    except Exception:
        app.logger.exception('action guard audit failed')


def _effort_change_needs_respawn(app: Flask, manager, task_id: str, images) -> bool:
    """True when a live, idle session must respawn to apply a new effort.

    Only fires for an explicit override that differs from the running
    session's effort, with no live turn in flight and no images (the
    respawn path can't carry images, so those deliver at the current
    effort and the change applies on the next plain message).
    """
    if images:
        return False
    overrides = app.config.get('TASK_EFFORT_OVERRIDES') or {}
    requested = str(overrides.get(task_id, '') or '')
    if not requested:
        return False  # Auto / no override — never force a respawn
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not getattr(session, 'is_alive', False):
        return False  # no live session — the spawn path applies the effort
    if bool(getattr(session, 'is_working', False)):
        return False  # don't interrupt a turn
    return str(getattr(session, 'effort', '') or '') != requested


def _model_change_needs_respawn(app: Flask, manager, task_id: str, images) -> bool:
    """True when a live, idle session must respawn to apply a new model.

    Same shape as ``_effort_change_needs_respawn``: the Claude CLI bakes
    ``--model`` at spawn time, so the operator changing the picker only
    takes effect on a fresh subprocess. Without this check the chat send
    route forwards the message into a subprocess still wired to the OLD
    model — and if that old model is no longer available to the
    operator's credentials the CLI errors out with "model X may not
    exist or you may not have access" (operator report: "I changed
    model to opus, why shutting").

    Only fires when an explicit override is set, differs from what the
    live session was spawned with, the session is idle, and there are no
    images (the respawn path can't carry them).
    """
    if images:
        return False
    overrides = app.config.get('TASK_MODEL_OVERRIDES') or {}
    requested = str(overrides.get(task_id, '') or '')
    if not requested:
        return False  # no override — never force a respawn
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not getattr(session, 'is_alive', False):
        return False  # no live session — the spawn path applies the model
    if bool(getattr(session, 'is_working', False)):
        return False  # don't interrupt a turn
    return str(getattr(session, 'model', '') or '') != requested


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
    overrides = app.config.get('TASK_MODEL_OVERRIDES') or {}
    model_override = overrides.get(task_id, '')
    effort_overrides = app.config.get('TASK_EFFORT_OVERRIDES') or {}
    # No per-task override (or it was cleared) → pass the concrete chat default
    # explicitly, so kato never falls through to the CLI's opaque built-in
    # effort (the old "Auto"). The operator always knows the level that ran.
    effort_override = effort_overrides.get(task_id, '') or _configured_chat_effort(app)
    try:
        runner.resume_session_for_chat(
            task_id=task_id,
            message=text,
            cwd=cwd,
            task_summary=summary,
            additional_dirs=additional_dirs,
            model=model_override,
            effort=effort_override,
        )
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    return jsonify({'status': 'spawned', 'text': text})


def _task_has_active_comment_run(app, task_id: str) -> bool:
    """Is a kato comment-run occupying — or queued to occupy — the session?

    QUEUED counts as blocking too: a fresh-chat detach leaves a blank
    record, and the comment watcher's next 2-second tick would dispatch
    the queued comment as a fresh spawn whose id gets pinned as the
    ACTIVE chat — the operator's "new chat" would open as a comment-fix
    conversation. Conservative on failure (False): the chats switch
    should not be bricked by a comment-store hiccup — the UI's own
    mid-turn confirm still stands between the operator and a blind kill.
    """
    agent_service = app.config.get('AGENT_SERVICE')
    list_comments = getattr(agent_service, 'list_task_comments', None)
    if not callable(list_comments):
        return False
    try:
        comments = list_comments(task_id)
    except Exception:
        return False
    return any(
        str(comment.get('kato_status', '') or '') in ('in_progress', 'queued')
        for comment in comments
        if isinstance(comment, dict)
    )


def _claude_session_metadata_by_id(wanted_ids):
    """Map session id → on-disk transcript metadata for the given ids.

    Best-effort: ids whose JSONL isn't on disk simply have no entry, and
    any index failure returns an empty map — the chats list still renders
    with bare ids. When one session id has multiple transcript copies on
    disk (cwd-drift snapshots, adopt copies), keep the NEWEST — the older
    snapshot would otherwise win the dict insert and show stale previews.
    """
    if not wanted_ids:
        return {}
    from claude_core_lib.claude_core_lib.session.index import list_sessions

    try:
        rows = list_sessions(max_results=10000)
    except Exception:
        return {}
    best: dict = {}
    for row in rows:
        if row.agent_session_id not in wanted_ids:
            continue
        current = best.get(row.agent_session_id)
        if current is None or row.last_modified_epoch > current.last_modified_epoch:
            best[row.agent_session_id] = row
    return best


def _migrate_adopted_session_transcript(
    app, task_id: str, agent_session_id: str,
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
        if same_session_id(entry.agent_session_id, agent_session_id):
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
    """Sibling-repo ``--add-dir`` paths for the chat spawn.

    Thin alias over the shared ``sibling_repository_dirs`` helper so the
    chat-send route and the comment-run respawn surface the SAME repo set
    (a multi-repo task's agent must reach every repo, not just ``cwd``).
    """
    from kato_core_lib.helpers.workspace_repo_utils import (
        sibling_repository_dirs,
    )
    return sibling_repository_dirs(workspace_manager, task_id, cwd)


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


def _event_stream_generator(
    manager, workspace_manager, task_id: str, agent_service=None,
):
    """Yield SSE frames for one tab's session.

    Lifecycle outcomes:
      * `session_missing`  — no record AND no workspace exists for this task.
      * `session_idle`     — workspace/record exists but no live subprocess
        (history replayed from Claude's JSONL so the chat shows past turns).
      * (live stream + `session_closed`) — events flow until the
        subprocess exits and the buffer drains.
    """
    agent_session_id = _resolve_agent_session_id(
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
        yield from _replay_history_from_disk(agent_session_id)
        if _drain_queued_task_comment(agent_service, task_id):
            session = manager.get_session(task_id) if manager is not None else None
            if session is not None:
                replayed_count = yield from _replay_session_backlog(
                    session, agent_service=agent_service, task_id=task_id,
                )
                yield from _follow_live_session(
                    session, start_index=replayed_count,
                    agent_service=agent_service, task_id=task_id,
                )
                return
        idle_payload = _record_to_dict(record) if record is not None else {}
        yield _sse_message(SSE_EVENT_SESSION_IDLE, idle_payload)
        return
    yield from _replay_preflight_log(workspace_manager, task_id)
    yield from _replay_history_from_disk(agent_session_id)
    replayed_count = yield from _replay_session_backlog(
        session, agent_service=agent_service, task_id=task_id,
    )
    yield from _follow_live_session(
        session, start_index=replayed_count,
        agent_service=agent_service, task_id=task_id,
    )


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


# Resolver lives in claude_core_lib (reads the ``agent_session_id``
# field set by ClaudeSessionManager and feeds the downstream replay
# of Claude's JSONL transcripts).
from claude_core_lib.claude_core_lib.session.history import (
    resolve_agent_session_id as _resolve_agent_session_id,  # noqa: F401 — kept as alias for in-file callers
)


def _replay_history_from_disk(agent_session_id: str):
    if not agent_session_id:
        return
    try:
        from claude_core_lib.claude_core_lib.session.history import load_history_events
    except ImportError:
        return
    try:
        events = load_history_events(agent_session_id)
    except Exception:
        return
    # Emit under a distinct event type so the client doesn't run these
    # through the live-state reducer (otherwise an archived ``assistant``
    # event would set turnInFlight=true forever). Carry the JSONL line's
    # ``timestamp`` as ``received_at_epoch`` so replayed prompts show WHEN
    # they were asked (0 would hide the time after a reload). It is NOT the
    # dedupe key for history (a content fingerprint is — see keyForEntry),
    # so this is display-only.
    for raw in events:
        yield _sse_message(
            SSE_EVENT_SESSION_HISTORY_EVENT,
            {'event': {
                'received_at_epoch': _epoch_from_iso(raw.get('timestamp')),
                'raw': raw,
            }},
        )


def _epoch_from_iso(value) -> float:
    """Best-effort ISO-8601 → epoch seconds; 0.0 on anything unparseable.

    Claude JSONL lines carry a ``timestamp`` like
    ``2026-06-08T14:32:00.000Z`` (Python 3.11's ``fromisoformat`` accepts the
    trailing ``Z``)."""
    text = str(value or '').strip()
    if not text:
        return 0.0
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(text)
        # A naive (offset-less) timestamp would otherwise be read as LOCAL
        # time → wrong epoch. Claude always emits the trailing ``Z``, but assume
        # UTC if a future format ever drops it.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OSError):
        return 0.0


def _session_event_frame(event, session) -> str:
    """Serialise a session event for SSE, annotating ``control_request``
    events with the Action Guard risk so the permission modal can render the
    category/reason. Annotates a COPY of the raw dict so the shared stored
    event is never mutated from the SSE thread."""
    payload = event.to_dict()
    raw = payload.get('raw') if isinstance(payload, dict) else None
    if isinstance(raw, dict) and raw.get('type') == CLAUDE_EVENT_CONTROL_REQUEST:
        raw = dict(raw)
        _annotate_action_guard(raw, session)
        payload = {**payload, 'raw': raw}
    return _sse_message(SSE_EVENT_SESSION_EVENT, {'event': payload})


def _replay_session_backlog(session, agent_service=None, task_id=''):
    """Catch a freshly-connecting browser up on everything seen so far.

    UI catch-up ONLY — it must NOT drive comment completion. Replaying
    the backlog re-walks OLD ``result`` events (and a resumed session's
    history carries the prior turn's result too); feeding those into
    ``_advance_task_comments_after_result`` attributed a stale, unrelated
    answer to whatever comment was currently IN_PROGRESS and flipped its
    badge to ADDRESSED mid-work. Comment completion is driven only by
    LIVE results (``_follow_live_session``) and the server-side scan-loop
    fallback (``advance_finished_comment_runs``), both of which run while
    the session is idle and attach the comment's OWN result. ``agent_service``
    / ``task_id`` are accepted for call-site symmetry but intentionally unused.
    """
    backlog = session.recent_events()
    for event in backlog:
        yield _session_event_frame(event, session)
    return len(backlog)


def _follow_live_session(
    session, start_index: int = 0, agent_service=None, task_id: str = '',
):
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
            yield _session_event_frame(event, session)
            _advance_task_comments_after_result(event, agent_service, task_id)

        if not session.is_alive:
            # Drain any final events that landed between the slice
            # and ``is_alive`` flipping, then close.
            tail, last_index = session.events_after(last_index)
            for event in tail:
                yield _session_event_frame(event, session)
                _advance_task_comments_after_result(event, agent_service, task_id)
            yield _sse_message(SSE_EVENT_SESSION_CLOSED, {})
            return

        if time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
            yield ': ping\n\n'
            last_heartbeat = time.monotonic()

        time.sleep(_SSE_POLL_INTERVAL_SECONDS)


def _advance_task_comments_after_result(event, agent_service, task_id: str) -> None:
    """On a turn-end RESULT: finish the in-progress comment, then
    release the next queued one.

    The turn that just ended is the one kato handed the in-progress
    comment to. Without the completion step a comment kato actually
    finished stayed on the "kato working" badge forever (and a
    restart would redo it). Completion runs BEFORE the drain so the
    next queued comment enters a clean state.
    """
    event_type = getattr(event, 'event_type', '')
    raw = getattr(event, 'raw', {}) or {}
    if event_type != CLAUDE_EVENT_RESULT and raw.get('type') != CLAUDE_EVENT_RESULT:
        return
    success = not bool(raw.get('is_error', False))
    result_text = str(raw.get('result') or '')
    _complete_in_progress_task_comments(
        agent_service,
        task_id,
        success,
        result_text=result_text,
        result_received_at_epoch=float(
            getattr(event, 'received_at_epoch', 0.0) or 0.0,
        ),
    )
    _drain_queued_task_comment(agent_service, task_id)


def _complete_in_progress_task_comments(
    agent_service,
    task_id: str,
    success: bool,
    result_text: str = '',
    result_received_at_epoch: float = 0.0,
) -> None:
    complete = getattr(
        agent_service, 'complete_in_progress_task_comments', None,
    )
    if not callable(complete):
        return
    try:
        complete(
            task_id,
            success=success,
            result_text=result_text,
            result_received_at_epoch=result_received_at_epoch,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            'completing in-progress comments failed for task %s', task_id,
        )


def _drain_queued_task_comment(agent_service, task_id: str) -> bool:
    drain = getattr(agent_service, 'drain_next_queued_task_comment', None)
    if not callable(drain):
        return False
    try:
        result = drain(task_id)
    except Exception:
        logging.getLogger(__name__).exception(
            'queued comment drain failed for task %s', task_id,
        )
        return False
    if not isinstance(result, dict):
        return False
    return bool(result.get('started'))


def _sse_response(generator, *, accel: bool = True) -> Response:
    """Wrap an SSE generator in the standard ``text/event-stream`` Response.

    Always streams ``generator`` via ``stream_with_context`` with
    ``Cache-Control: no-cache, no-transform``. When ``accel`` is true
    (the live session + status feeds), also sets
    ``X-Accel-Buffering: no`` so nginx doesn't buffer the stream; the
    "feed disabled" one-shot stream passes ``accel=False`` to match its
    header set exactly.
    """
    headers = {'Cache-Control': 'no-cache, no-transform'}
    if accel:
        headers['X-Accel-Buffering'] = 'no'
    return Response(
        stream_with_context(generator),
        mimetype='text/event-stream',
        headers=headers,
    )


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
    ``agent_session_id`` (back-fill from session-manager records when
    older workspace metadata didn't capture it yet), and
    ``has_changes_pending`` (true when kato is paused awaiting push
    approval — the workspace has commits ready to push).
    """
    from kato_core_lib.helpers.lessons_path_utils import (
        is_reserved_workspace_dirname,
    )
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
    # Skip kato's own lessons-state dirs (``lessons/`` / ``lesson-candidates/``)
    # — they live inside KATO_WORKSPACES_ROOT next to the task clones, so the
    # workspace walk lists them, but they are NOT tasks and must never show as
    # tabs (the "lesson-candidates"/"lessons" phantom-tab bug). Lessons stay in
    # files; they just don't get a UI tab.
    workspace_records = [
        record for record in workspace_manager.list_workspaces()
        if not is_reserved_workspace_dirname(getattr(record, 'task_id', ''))
    ]
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


def _iter_live_sessions(session_manager):
    """Yield ``(record, session)`` for every record whose session resolves.

    Shared best-effort walk behind the per-task session accumulators
    (``_live_session_ids`` / ``_working_session_ids`` /
    ``_pending_permission_tool_by_task``): a ``None`` manager or a
    ``list_records`` failure yields nothing, a per-record
    ``get_session`` failure is skipped, and ``None`` sessions are
    dropped. ``_session_ids_by_task`` is intentionally NOT built on
    this — it never calls ``get_session`` (it keys off the record's
    own stored agent_session_id), so it has no session to yield.
    """
    if session_manager is None:
        return
    try:
        records = session_manager.list_records()
    except Exception:
        return
    for record in records:
        try:
            session = session_manager.get_session(record.task_id)
        except Exception:
            continue
        if session is None:
            continue
        yield record, session


def _session_ids_by_task(session_manager) -> dict[str, str]:
    if session_manager is None:
        return {}
    try:
        records = session_manager.list_records()
    except Exception:
        return {}
    return {
        str(record.task_id): read_session_id_from(record)
        for record in records
        if read_session_id_from(record)
    }


def _working_session_ids(session_manager) -> set[str]:
    """Subset of ``_live_session_ids`` whose Claude turn is in flight.

    The sidebar tab dot uses this to dim a tab whose subprocess is alive
    but not actively producing — operator can tell at a glance whether
    Claude is still chewing on a turn or just waiting for input.
    """
    working: set[str] = set()
    for record, session in _iter_live_sessions(session_manager):
        if getattr(session, 'is_working', False):
            working.add(record.task_id)
    return working


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
    pending: dict[str, str] = {}
    for record, session in _iter_live_sessions(session_manager):
        tool_name = _session_pending_permission_tool(session)
        if tool_name:
            # Empty-string tool name still marks pending (legacy
            # callers + back-compat) — the UI's filter just can't
            # match it to a remembered decision.
            pending[record.task_id] = tool_name
    return pending


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
    live: set[str] = set()
    for record, session in _iter_live_sessions(session_manager):
        if getattr(session, 'is_alive', False):
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
    if not payload.get(AGENT_SESSION_ID) and session_ids_by_task:
        backfilled = session_ids_by_task.get(record.task_id, '')
        if backfilled:
            payload[AGENT_SESSION_ID] = backfilled
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
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    return {'task_id': str(getattr(record, 'task_id', '') or '')}


def _build_fallback_manager(fallback_state_dir: str):
    """Stand up a minimal manager so dev runs of the webserver don't crash."""
    try:
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
    except ImportError:                 # pragma: no cover — claude_core_lib is always installed in this repo; the fallback exists for embedded webserver use outside the kato monorepo
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


if __name__ == '__main__':  # pragma: no cover - module-as-script guard, never hit under import-based tests
    main()
