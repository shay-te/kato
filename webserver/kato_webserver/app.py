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
    POST /api/sessions/<task_id>/permission             — body: {"request_id", "allow", "rationale", "remember"}
    GET  /api/tool-decisions                            — list remembered "allow/deny always" decisions
    POST /api/tool-decisions/set                        — body: {"tool_name", "command_signature", "allow"}
    POST /api/tool-decisions/forget                      — body: {"tool_name", "command_signature"}
    POST /api/tool-decisions/clear                       — clear every remembered decision
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
    POST /api/sessions/<task_id>/comments/<id>/retry    — re-run a FAILED comment
    DEL  /api/sessions/<task_id>/comments/<id>          — delete comment + replies
    POST /api/sessions/<task_id>/comments/<id>/edit     — edit queued local comment body / kato_status
    POST /api/sessions/<task_id>/comments/sync          — git pull + pull remote PR comments
    GET  /api/claude/sessions                           — list adoptable Claude Code sessions
    GET  /api/status/events                             — SSE: live kato-process log feed
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping
from kato_core_lib.helpers.explain_mode_utils import (
    EXPLAIN_MODE,
    is_explain_mode,
    session_is_in_explain_mode,
)
from kato_core_lib.helpers.kato_paths_utils import kato_session_state_dir
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
    has_origin_remote,
    list_branch_commits,
    resolve_base_ref,
    task_folder_file_tree,
    tracked_file_tree,
)
from kato_webserver.prompt_attachment_store import save_attachment
from kato_webserver.prompt_draft_store import read_draft, write_draft


REPO_ROOT = Path(__file__).resolve().parents[1]
KATO_REPO_ROOT = REPO_ROOT.parent
if getattr(sys, 'frozen', False):
    # Desktop sidecar only (PyInstaller one-file): bundled assets unpack under
    # sys._MEIPASS, where the freeze ships static/, templates/ and kato.png at
    # the root. Anchor both roots there so /static, /templates and the
    # /logo.png + /favicon.png routes resolve. No effect on a normal run.
    REPO_ROOT = KATO_REPO_ROOT = Path(sys._MEIPASS)

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
# Claude's planning-only permission mode (``claude --permission-mode plan``):
# the agent may read / search / plan but never edit files or run mutating
# tools. Backs the composer's plan-mode lock.
PLAN_PERMISSION_MODE = 'plan'
# Agent modes the composer can select, as the CLI's ``--permission-mode``.
# '' = kato's configured default (acceptEdits) — the composer's "Edit
# automatically". Anything outside this set is refused at the route rather
# than handed to the CLI, where it would break the spawn instead.
# ``EXPLAIN_MODE`` is the one entry that is NOT a CLI permission mode: it is a
# kato-level mode that the spawn path resolves into a permission mode plus a
# read-only tool split (see helpers/explain_mode_utils.py). It is accepted here
# because this set is what the COMPOSER may select, not what reaches the CLI.
AGENT_PERMISSION_MODES = frozenset({
    '', 'default', 'acceptEdits', PLAN_PERMISSION_MODE, 'bypassPermissions',
    EXPLAIN_MODE,
})


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


def _resolve_setting(key: str) -> dict:
    """Resolve one settings key across kato's two stores.

    Precedence mirrors boot: live ``os.environ`` (shell or
    already-loaded) > ``~/.kato/settings.json``. Returns
    ``{value, source}`` where ``source`` is one of ``env`` /
    ``kato_settings`` / ``unset`` so the UI can label where a value
    lives. (``.env`` support was removed — settings.json is kato's
    only config file.)
    """
    from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

    live = os.environ.get(key, '')
    settings_value = read_kato_settings().get(key, '')
    if live:
        value, source = live, 'env'
    elif settings_value:
        value, source = settings_value, 'kato_settings'
    else:
        value, source = '', 'unset'
    return {
        'value': value,
        'source': source,
    }


def _validate_settings(updates: dict[str, str]) -> list[str]:
    from kato_core_lib.helpers.kato_settings_schema_utils import (
        validate_settings_values,
    )
    return validate_settings_values(updates)


def _validate_persist_and_respond(updates: dict, app: Flask | None = None):
    """Validate → persist → standard "saved, restart required" response.

    Shared tail for the three settings-write POST handlers
    (task-providers / git-providers / all-settings). On validation
    failure returns the ``400`` ``'; '``-joined error; on a write
    ``OSError`` returns the ``500`` failure body; otherwise the
    ``{ok, updated_keys, restart_required, message}`` success envelope.
    Callers own their allowlist filtering and the "no recognised
    updates" empty-payload guard — this is purely the common tail.

    ``app`` (when passed) lets a saved key take effect immediately
    instead of at the next restart — see :func:`_apply_live_settings`.
    """
    validation_errors = _validate_settings(updates)
    if validation_errors:
        return jsonify({'error': '; '.join(validation_errors)}), 400
    try:
        _persist_settings(updates)
    except OSError as exc:
        return jsonify({'error': f'failed to write settings file: {exc}'}), 500
    live_message = _apply_live_settings(app, updates) if app is not None else ''
    # A save can mix a live key with restart-only ones (the General tab
    # POSTs every dirty field at once) — the banner must stay up unless
    # EVERY saved key applies live, or a restart-only change silently
    # looks like it already took effect.
    restart_required = not (live_message and set(updates) <= _live_settings_keys())
    message = live_message or 'Saved. Restart kato for the change to take effect.'
    if live_message and restart_required:
        message = f'{live_message} Restart kato for the other change(s).'
    return jsonify({
        'ok': True,
        'updated_keys': sorted(updates.keys()),
        'restart_required': restart_required,
        'message': message,
    })


def _live_settings_keys() -> frozenset[str]:
    """Schema keys :func:`_apply_live_settings` enforces without a restart."""
    from kato_core_lib.helpers.review_comment_gate_utils import (
        REVIEW_COMMENTS_ENABLED_KEY,
    )

    return frozenset({REVIEW_COMMENTS_ENABLED_KEY})


def _apply_live_settings(app: Flask, updates: dict) -> str:
    """Enforce the just-saved settings that apply WITHOUT a restart.

    Returns the operator-facing message to send back, or ``''`` when
    nothing live was touched (caller falls back to "restart required").

    Today that is the review-comment switch. Turning it off has to do two
    things: stop the next poll (the gate in ``review_comment_gate_utils``
    reads settings.json fresh, so persisting already did that) and stop the
    run already going — which needs the live service, hence this hook.
    """
    from kato_core_lib.helpers.review_comment_gate_utils import (
        REVIEW_COMMENTS_ENABLED_KEY,
    )

    if REVIEW_COMMENTS_ENABLED_KEY not in updates:
        return ''
    turned_on = str(updates[REVIEW_COMMENTS_ENABLED_KEY]).strip().lower() != 'false'
    if turned_on:
        return 'Saved. Kato will pull PR review comments on the next scan.'
    stopped = _stop_review_comment_work(app)
    if not stopped:
        return 'Saved. Kato has stopped pulling PR review comments.'
    return (
        'Saved. Kato has stopped pulling PR review comments and stopped '
        f'{len(stopped)} run(s) in progress: {", ".join(stopped)}.'
    )


def _stop_review_comment_work(app: Flask) -> list[str]:
    """Best-effort stop of in-flight review-comment runs. Never raises —
    the settings write already succeeded and must not report failure
    because a teardown hiccuped; the gate still blocks the next poll."""
    service = app.config.get('AGENT_SERVICE')
    stopper = _agent_method(service, 'comments.stop_review_comment_work')
    if not callable(stopper):
        return []
    try:
        stopped = stopper()
    except Exception:
        app.logger.exception(
            'failed to stop in-flight review-comment runs after the switch '
            'was turned off',
        )
        return []
    return [str(task_id) for task_id in (stopped or [])]


def _persist_settings(updates: dict) -> None:
    """Write UI-edited settings to ``~/.kato/settings.json`` (atomic).

    Single chokepoint so every settings route writes the same place.

    Also mirrors the saved values into ``os.environ``. Boot copies
    settings.json INTO the process env (``_load_layered_config_into_environ``),
    and ``_resolve_setting`` reads env first — so without this mirror a key
    that existed at boot keeps reporting its BOOT value after a save, and the
    UI redraws the field with the old value the moment it refreshes. That
    reads as "my change didn't save". Mirroring keeps the two stores saying
    the same thing inside the live process; anything already read at boot is
    unaffected, and live readers now see what the operator actually chose.
    """
    from kato_core_lib.helpers.kato_settings_store_utils import write_kato_settings

    write_kato_settings(updates)
    for key, value in updates.items():
        os.environ[str(key)] = str(value)


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
# The same underlying env keys back both — e.g. editing
# ``BITBUCKET_API_TOKEN`` in either tab writes the same key. That's
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
        'YOUTRACK_DONE_STATE_FIELD',
        'YOUTRACK_DONE_STATE',
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
        'JIRA_DONE_STATE_FIELD',
        'JIRA_DONE_STATE',
        'JIRA_ISSUE_STATES',
    ),
    'github': (
        'GITHUB_API_BASE_URL',
        'GITHUB_API_TOKEN',
        'GITHUB_API_TOKEN_SOURCE',
        'GITHUB_OWNER',
        'GITHUB_REPO',
        'GITHUB_ASSIGNEE',
        'GITHUB_PROGRESS_STATE_FIELD',
        'GITHUB_PROGRESS_STATE',
        'GITHUB_REVIEW_STATE_FIELD',
        'GITHUB_REVIEW_STATE',
        'GITHUB_DONE_STATE_FIELD',
        'GITHUB_DONE_STATE',
        'GITHUB_ISSUE_STATES',
    ),
    'gitlab': (
        'GITLAB_API_BASE_URL',
        'GITLAB_API_TOKEN',
        'GITLAB_API_TOKEN_SOURCE',
        'GITLAB_PROJECT',
        'GITLAB_ASSIGNEE',
        'GITLAB_PROGRESS_STATE_FIELD',
        'GITLAB_PROGRESS_STATE',
        'GITLAB_REVIEW_STATE_FIELD',
        'GITLAB_REVIEW_STATE',
        'GITLAB_DONE_STATE_FIELD',
        'GITLAB_DONE_STATE',
        'GITLAB_ISSUE_STATES',
    ),
    'bitbucket': (
        'BITBUCKET_API_BASE_URL',
        'BITBUCKET_API_TOKEN',
        'BITBUCKET_API_TOKEN_SOURCE',
        'BITBUCKET_USERNAME',
        'BITBUCKET_API_EMAIL',
        'BITBUCKET_WORKSPACE',
        'BITBUCKET_REPO_SLUG',
        'BITBUCKET_ASSIGNEE',
        'BITBUCKET_PROGRESS_STATE_FIELD',
        'BITBUCKET_PROGRESS_STATE',
        'BITBUCKET_REVIEW_STATE_FIELD',
        'BITBUCKET_REVIEW_STATE',
        'BITBUCKET_DONE_STATE_FIELD',
        'BITBUCKET_DONE_STATE',
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
        'BITBUCKET_API_TOKEN_SOURCE',
        'BITBUCKET_USERNAME',
        'BITBUCKET_API_EMAIL',
        'BITBUCKET_WORKSPACE',
        'BITBUCKET_REPO_SLUG',
    ),
    'github': (
        'GITHUB_API_BASE_URL',
        'GITHUB_API_TOKEN',
        'GITHUB_API_TOKEN_SOURCE',
        'GITHUB_OWNER',
        'GITHUB_REPO',
    ),
    'gitlab': (
        'GITLAB_API_BASE_URL',
        'GITLAB_API_TOKEN',
        'GITLAB_API_TOKEN_SOURCE',
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
    missing becomes the empty string the settings layer expects).
    ``fields`` is expected to already be a validated dict.
    """
    allowed = set(field_map[provider])
    return {
        key: str(value or '')
        for key, value in fields.items()
        if key in allowed
    }




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
    full_paths=(),
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
    ref, is_local = resolve_base_ref(cwd, base)
    # A repo WITH an origin remote but no resolvable base is a genuine config
    # problem (wrong/missing destination_branch) — surface the guiding error
    # rather than silently HEAD-diffing and hiding committed task-branch work.
    # A repo with NO origin remote is a local-only clone the operator dropped
    # into the task: there is no cloud base, so diff the working tree against
    # HEAD to show the edits they see in their editor.
    if is_local and has_origin_remote(cwd):
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
        'base': '' if is_local else base,
        'head': current_branch(cwd),
        'diff': diff_against_base(cwd, ref, full_paths=full_paths),
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
        lookup = getattr(getattr(agent_service, 'publish', agent_service), 'configured_destination_branch', None)
        if callable(lookup):
            configured = (lookup(repo_id) or '').strip()
            if configured:
                return configured
    return detect_default_branch(cwd)


def _changed_files_for_repo(repo_id: str, cwd: str, agent_service) -> list[str]:
    """Changed-vs-base file list for the Files tree, base-resolved
    the same way the Changes tab does so the two never disagree.

    Read-only (no ``ensure_branch_checked_out``): the Files tab must
    not mutate git state. Empty list when a base is required (repo has a
    remote) but can't be resolved — the tree just renders without change
    colouring. A no-remote local clone falls back to a HEAD diff so its
    working-tree edits still colour the tree (matches the Changes tab).
    """
    base = _resolve_diff_base(repo_id, cwd, agent_service)
    ref, is_local = resolve_base_ref(cwd, base)
    if is_local and has_origin_remote(cwd):
        return []
    return changed_paths(cwd, ref)


def _finalize_resolved_merges(agent_service, task_id: str) -> None:
    """Best-effort: commit any pending merge whose conflicts are resolved.

    Called at the top of the polled Files / Changes reads so a merge the
    agent just resolved gets finalised (it can't run git itself), leaving
    the diff showing only the branch's work. A cheap no-op when no merge is
    pending. Never lets a git hiccup break the read the operator is waiting
    on.
    """
    if agent_service is None:
        return
    try:
        agent_service.publish.finalize_resolved_merges_for_task(task_id)
    except Exception:
        logging.getLogger(__name__).exception(
            'finalize-resolved-merges failed for %s', task_id,
        )


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


def _requested_chat_backend(payload) -> str:
    """The backend an operator picked for a new chat, or ``''``.

    Validated against the known names rather than trusted: this value is
    persisted on the record and used to route every later turn, so an
    unrecognised string would route nowhere for the life of the chat.
    """
    from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
    backend = AgentBackend.parse((payload or {}).get('agent_backend'))
    return backend.value if backend is not None else ''


def _agent_method(agent_service, method_name: str):
    """Look up ``method_name`` on the agent service — dotted names allowed.

    A dotted name addresses a SUB-SERVICE: ``comments.add_task_comment``,
    ``publish.push_task``, ``repositories.sync_task_repositories``. Each
    subsystem lives on its own object, and routes name it that way instead of
    relying on a pass-through method being kept on the facade.

    Returns ``None`` when any hop is missing, so callers decide whether that
    is a 501 or a soft empty payload. A service (or a test double) that
    predates the sub-service split still answers the bare name on the facade,
    so a missing hop falls back to it.
    """
    holder = target = agent_service
    for part in method_name.split('.'):
        holder, target = target, getattr(target, part, None)
        if target is None:
            return getattr(holder, method_name.rsplit('.', 1)[-1], None)
    return target


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
    method = _agent_method(agent_service, method_name)
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


#: A backend id that may be folded into a per-task override key.
_BACKEND_KEY_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def _override_key(app: Flask, task_id: str) -> str:
    """Store key for a per-task override: the task AND its active backend.

    Model and effort are backend-specific — ``opus`` means nothing to Codex,
    which answers a request for it with "The 'opus' model is not supported
    when using Codex with a ChatGPT account" and fails the turn. Keying these
    on ``task_id`` alone made switching a task to the Codex tab inherit the
    Claude model the operator had picked, so the first message died before it
    reached the model.

    Falls back to the bare task id when the backend is unknown, which keeps
    the key stable for a single-backend host.
    """
    backend = ''
    manager = app.config.get('SESSION_MANAGER')
    resolver = getattr(manager, 'backend_for', None)
    if callable(resolver):
        try:
            backend = str(resolver(task_id) or '').strip().lower()
        except Exception:
            backend = ''
    # Only a plausible backend identifier is allowed into the key. Anything
    # else — a stub that returns an object, a resolver that answers with a
    # repr — would mint an unstable key, and the operator's saved model would
    # silently read back empty on the very next request.
    if not _BACKEND_KEY_RE.match(backend):
        backend = ''
    return f'{task_id}::{backend}' if backend else str(task_id)


def _get_task_override(app: Flask, key: str, task_id: str) -> str:
    """Read a per-task override (model / effort) from its config store.

    Treats a missing / ``None`` store as "no override" — returns the
    empty string, matching the GET routes' ``... or {}`` fallback.
    """
    return (app.config.get(key) or {}).get(_override_key(app, task_id), '')


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
    store_key = _override_key(app, task_id)
    if value:
        store[store_key] = value
    else:
        store.pop(store_key, None)
    return True


# Only OPTIONS is exempt: Flask/Werkzeug answers it automatically
# (allowed-methods introspection) without ever running a view
# function, so there is no route body — and therefore no side
# effect — it could reach. Every other method, INCLUDING GET/HEAD,
# is guarded: see the docstring below for why "GET never mutates"
# does not actually hold in this app.
_CSRF_EXEMPT_HTTP_METHODS = frozenset({'OPTIONS'})

#: ``Sec-Fetch-Site`` values that may reach the app. ``same-origin`` is the UI
#: itself; ``none`` is a user-initiated navigation (typed URL, bookmark) — the
#: same case the missing-header bail already lets through. ``same-site`` is
#: deliberately absent: the Origin compare is per-netloc, so another port on
#: the same host is cross-origin to it, and this must not be the looser rule.
_CSRF_ALLOWED_FETCH_SITES = frozenset({'same-origin', 'none'})


def _is_page_navigation(req) -> bool:
    """A TOP-LEVEL navigation to a page — not an API call.

    Carved out of the fetch-metadata check for two reasons, one of them
    load-bearing for the desktop app.

    The Tauri shell points its webview at ``http://localhost:<port>/?_=<nonce>``
    via ``WebviewUrl::External`` (desktop/src-tauri/src/main.rs). That is an
    embedder-initiated navigation, and the platform webviews (WKWebView on
    macOS, WebView2 on Windows) are not obliged to label it ``none`` the way a
    typed URL in a browser is. If one of them reports ``cross-site``, a strict
    check would answer the desktop app's very first request with a 403 and the
    operator would see an error page instead of kato.

    It is also not a CSRF vector. A top-level navigation loads a DOCUMENT the
    initiating page cannot read — no response body crosses back to it. The
    vector this guard exists to stop is the SUBRESOURCE one (``<img>``,
    ``fetch``), which is never ``Sec-Fetch-Mode: navigate``.

    ``/api/*`` is deliberately excluded from the carve-out: those routes have
    real side effects (permission auto-resolve, comment dispatch, merge
    commits), and while a navigation to one is noisy — it opens a visible
    window — there is no reason to permit it.
    """
    return (
        req.headers.get('Sec-Fetch-Mode') == 'navigate'
        and not req.path.startswith('/api/')
    )


def _register_csrf_guard(app: Flask) -> None:
    """Reject cross-origin requests — including GET.

    Kato has no login/session, so the risk here isn't a stolen
    authenticated cookie (there isn't one) — it's a page open in the
    operator's own browser using that browser as a network pivot to
    reach the loopback-bound API a remote attacker's own machine can't
    reach directly (e.g. a malicious page running
    ``fetch('http://127.0.0.1:5050/api/...', {method: 'POST', ...})``).
    Browsers attach ``Origin`` (or, failing that, ``Referer``) on every
    cross-origin request and on same-origin non-GET/HEAD requests too,
    so comparing it against the server's own host is a real defense
    even with zero authentication. A request with NEITHER header
    (curl, a local script, server-to-server calls, direct navigation —
    typing the URL or a bookmark) is let through — it carries no
    ambient browser context to abuse in the first place, so this
    check has nothing to defend against there.

    GET is deliberately NOT exempted, unlike a textbook CSRF guard.
    An audit turned up real GET-triggered mutations in this app:
    ``/files``/``/diff`` can commit a resolved merge, and
    ``/sessions``/``/permissions/pending``/the SSE stream can
    auto-resolve (approve/deny) a live agent's pending tool
    permission from a remembered decision — reachable via nothing
    more than ``<img src="http://127.0.0.1:5050/api/sessions">`` on
    any page the operator's browser visits, no JavaScript required.
    Browsers DO send ``Referer`` on a cross-origin ``<img>`` request
    even though they skip ``Origin`` for it — but ONLY under the default
    referrer policy, which the requesting page chooses. That made the
    Origin/Referer pair alone insufficient for GET: ``referrerpolicy=
    "no-referrer"`` on the image (or a document-wide ``<meta name=
    "referrer">``) suppresses both headers and lands on the
    no-header bail. ``Sec-Fetch-Site`` is checked first for exactly that
    reason — the browser sets it, the page cannot suppress it, and
    non-browser callers omit it so the bail still covers them.

    Comparison is allowlist-style (reject unless it resolves to OUR
    host), not blocklist-style (reject only a resolvable mismatch).
    ``Origin: null`` — sent by real browsers from an opaque-origin
    context (a sandboxed iframe without ``allow-same-origin``, a
    ``data:`` URI) — is the textbook null-origin CSRF bypass: it IS a
    header the attacker's page sent, but ``urlsplit('null').netloc``
    is ``''``, which a blocklist compare (``origin_host and ... !=``)
    silently treated identically to "no header sent at all." A header
    that was sent but doesn't resolve to a real host is exactly the
    suspicious case, not the safe one.
    """
    @app.before_request
    def _reject_cross_origin_request():
        if request.method in _CSRF_EXEMPT_HTTP_METHODS:
            return None
        # Fetch metadata FIRST, because the Origin/Referer pair below can be
        # switched off by the very page this guard exists to stop.
        #
        # The docstring's justification for covering GET — "browsers DO send
        # Referer on a cross-origin <img>" — holds only under the DEFAULT
        # referrer policy, and the requesting page picks the policy. All three
        # of these reach the ``if not origin: return None`` bail below with
        # neither header, and were allowed:
        #
        #     <img src="http://127.0.0.1:5050/api/sessions" referrerpolicy="no-referrer">
        #     <meta name="referrer" content="no-referrer">  (document-wide)
        #     fetch(url, {mode: 'no-cors', referrerPolicy: 'no-referrer'})
        #
        # ``Sec-Fetch-Site`` is a forbidden header name: the browser sets it,
        # page script cannot forge or clear it, and referrer policy has no
        # effect on it. Non-browser callers (curl, the /healthz probe,
        # server-to-server) omit it entirely, so the deliberate
        # no-ambient-context exemption below survives untouched.
        #
        # Only ``same-origin`` and ``none`` (direct navigation — typed URL or
        # bookmark) are accepted, matching what the Origin compare already
        # enforces: it compares ``netloc``, so a different PORT on the same
        # host is cross-origin and rejected. Accepting ``same-site`` here
        # would be looser than the check it backs up.
        #
        # Deliberately no ``Sec-Fetch-Dest`` rule: browsers send Dest and Site
        # together, so for the attack this closes, Dest adds nothing Site has
        # not already caught — and a Dest allowlist is one more thing to get
        # wrong against a future request type.
        fetch_site = request.headers.get('Sec-Fetch-Site', '')
        if (
            fetch_site
            and fetch_site not in _CSRF_ALLOWED_FETCH_SITES
            and not _is_page_navigation(request)
        ):
            return jsonify({'error': 'cross-origin request rejected'}), 403
        origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
        if not origin:
            return None
        origin_host = urlsplit(origin).netloc
        if origin_host != request.host:
            return jsonify({'error': 'cross-origin request rejected'}), 403
        return None


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
    needs_config=False,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / 'templates'),
        static_folder=str(REPO_ROOT / 'static'),
    )
    _register_csrf_guard(app)
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
    # True when kato booted UNCONFIGURED (setup mode): the webserver is up so
    # the operator can configure from the UI, but there's no ticket service
    # and no scan loop. The onboarding gate reads this via /api/config-status.
    app.config['NEEDS_CONFIG'] = bool(needs_config)
    # The last failed in-process start attempt's error (set by main's setup
    # wait loop, cleared on success) so the wizard can show WHY kato didn't
    # start instead of silently claiming "all set".
    app.config['SETUP_ERROR'] = ''
    app.config['TASK_MODEL_OVERRIDES'] = {}
    # Per-task chat effort override (Claude ``--effort`` level), set from
    # the composer's effort selector. Empty/absent => the configured
    # default. Applied on (re)spawn of the chat session.
    app.config['TASK_EFFORT_OVERRIDES'] = {}
    # Per-task "plan mode" lock, set from the composer's plan-mode toggle.
    # When on, the chat session spawns with ``--permission-mode plan`` so
    # the agent can ONLY plan — it never edits files or runs mutating
    # tools. Stored as the literal ``'plan'`` (on) or '' (off, the
    # configured default). Like model/effort the CLI bakes the mode at
    # spawn, so toggling it on a live idle session forces a respawn.
    #
    # Unlike model/effort, plan mode is a SAFETY lock, so it persists across
    # restarts: reload the locked tasks from ``plan_mode.json`` into the
    # live override map at boot, so the next respawn re-applies the lock.
    # Reloads EVERY persisted mode, not just the plan lock: the composer's
    # mode picker writes the literal ``--permission-mode`` here, so a task
    # left on "Manual" must come back on Manual rather than silently
    # dropping to the permissive default.
    from kato_core_lib.helpers.plan_mode_store import read_task_modes
    app.config['TASK_PLAN_MODE_OVERRIDES'] = dict(read_task_modes())

    # Per-task Remote Control preference (the composer's ``/`` menu, Claude
    # only): the set of tasks whose chat session should be reachable from
    # claude.ai / the Claude app. Unlike model / effort / permission-mode
    # this is NOT a spawn flag — it is a control request sent to the running
    # subprocess — but it still has to be remembered, because the bridge dies
    # with that subprocess and kato respawns chat sessions constantly. Loaded
    # from ``remote_control.json`` at boot and re-applied on every spawn.
    from kato_core_lib.helpers.remote_control_store import read_remote_control_tasks
    app.config['TASK_REMOTE_CONTROL'] = set(read_remote_control_tasks())

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
            app=app,
        ))

    @app.get('/api/models')
    def list_models():
        # Discovered, not hardcoded: Claude serves the stable CLI aliases
        # (opus/sonnet/haiku) with live version labels when a credential is
        # available; Codex reads its own model cache. The picker can no longer
        # show a stale version (it used to hardcode "Opus 4.7"). ``?refresh=1``
        # bypasses the discovery cache so the UI refresh updates labels live.
        return jsonify({
            'models': _discover_chat_models(
                app, force=_truthy_arg(request.args.get('refresh'))),
        })

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

    @app.post('/api/sessions/<task_id>/attachments')
    def upload_session_attachment(task_id: str):
        """Save a large composer attachment into the task workspace.

        Small text files are inlined into the prompt by the composer. Past a
        size threshold that only wastes context and truncates the interesting
        part, so the file is written to disk here and the prompt references
        its path instead — the agent reads it with its own tools.
        """
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'ok': False, 'error': 'not available'}), 503
        upload = request.files.get('file')
        if upload is None:
            return jsonify({'ok': False, 'error': 'no file in the request'}), 400
        try:
            workspace_dir = workspace_manager.workspace_path(task_id)
        except Exception:  # noqa: BLE001 - unknown task has nowhere to write
            return jsonify(
                {'ok': False, 'error': 'no workspace for this task'},
            ), 404
        result = save_attachment(workspace_dir, upload.filename, upload.read())
        return jsonify(result), (200 if result.get('ok') else 400)

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

    # NOTE: there is no ``/plan-mode`` route pair. It was the boolean ancestor
    # of ``/agent-mode`` below, superseded when Plan became one entry in the
    # composer's modes picker, and by then nothing called it: MessageForm took
    # ``planMode``/``onPlanModeChange`` as props and referenced neither, so the
    # GET fed state no one read and the POST was unreachable from any UI.
    #
    # Removed rather than left dormant because the POST was not inert. Both
    # routes wrote the SAME override map and the same ``plan_mode.json`` record
    # as ``/agent-mode``, but through a boolean — so ``{"plan_mode": false}``
    # wrote '' over whatever was there, including a ``bypassPermissions`` lock,
    # and then called ``_stop_live_session_on_tightening`` on the way out. A
    # dead endpoint that can clear a safety lock and kill a live session is
    # worse than one that merely wastes a request.
    #
    # Read "is this task plan-locked" as ``mode == 'plan'`` off ``/agent-mode``.

    @app.get('/api/sessions/<task_id>/agent-mode')
    def get_session_agent_mode(task_id: str):
        """The task's agent mode — the literal ``--permission-mode`` to spawn with.

        Empty string means "kato's configured default" (acceptEdits), which is
        what the composer shows as "Edit automatically".
        """
        return jsonify({
            'mode': _get_task_override(app, 'TASK_PLAN_MODE_OVERRIDES', task_id),
        })

    @app.post('/api/sessions/<task_id>/agent-mode')
    def set_session_agent_mode(task_id: str):
        """Set the task's agent mode. Persisted — it survives a restart.

        Rejects anything outside the known set rather than passing it to the
        CLI: an unrecognised ``--permission-mode`` makes the spawn fail, which
        would look like "kato stopped responding" long after the bad value was
        chosen.
        """
        body = request.get_json(silent=True) or {}
        mode = str(body.get('mode', '') or '').strip()
        if mode not in AGENT_PERMISSION_MODES:
            return jsonify({
                'error': f'unknown mode {mode!r}',
                'allowed': sorted(AGENT_PERMISSION_MODES),
            }), 400
        if not _set_task_override(app, 'TASK_PLAN_MODE_OVERRIDES', task_id, mode):
            return jsonify({'error': 'not available'}), 503
        # Best-effort persistence — a write failure must not fail the choice
        # the operator just made in the live session.
        from kato_core_lib.helpers.plan_mode_store import set_task_mode
        set_task_mode(task_id, mode)
        stopped = _stop_live_session_on_tightening(app, task_id, mode)
        return jsonify({'mode': mode, 'session_stopped': stopped})

    @app.get('/api/sessions/<task_id>/remote-control')
    def get_session_remote_control(task_id: str):
        """Remote Control status for a task's chat session.

        ``{supported, enabled, live, session_url, connect_url}``. ``enabled``
        is the operator's PREFERENCE (persisted); ``live`` says whether a
        running subprocess is actually bridged right now. The two differ
        legitimately — an idle tab has no subprocess, so the preference is on
        and nothing is bridged until the next message respawns it.
        """
        return jsonify(_remote_control_status(app, task_id))

    @app.post('/api/sessions/<task_id>/remote-control')
    def set_session_remote_control(task_id: str):
        """Turn Remote Control on/off for a task's chat session.

        Applies to the live subprocess when there is one, and is persisted
        either way so the next spawn re-applies it.
        """
        body = request.get_json(silent=True) or {}
        on = bool(body.get('enabled'))
        store = app.config.get('TASK_REMOTE_CONTROL')
        if store is None:
            return jsonify({'error': 'not available'}), 503
        if on and not _remote_control_supported(app, task_id):
            return jsonify({
                'error': 'this agent CLI does not support remote control',
            }), 400
        applied, error = _apply_remote_control(app, task_id, on)
        # Turning it OFF is persisted even when the live call failed: the
        # operator has said "stop exposing this session", and a preference
        # that stayed on would quietly re-bridge on the next spawn. Turning
        # it ON is only persisted once the CLI has actually agreed, so a
        # refusal (not signed in, CLI too old) never leaves kato claiming a
        # bridge that was never built.
        if not on or not error:
            _store_remote_control(app, task_id, on)
        status = _remote_control_status(app, task_id)
        if error:
            status['error'] = error
            return jsonify(status), 502
        status['applied'] = applied
        return jsonify(status)

    @app.get('/api/sessions/<task_id>/plan')
    def get_session_plan(task_id: str):
        """Return the agent's captured plan (``<workspace>/plan.md``).

        Written by ``ResumePromptWatcher`` whenever the agent presents a
        plan via ExitPlanMode (see ``plan_capture_utils`` / ``plan_writer``).
        The UI polls this to auto-open the plan in the centre pane for
        review — ``mtime`` drives the "a NEW plan just landed" detection so
        the view only auto-opens on a fresh plan, never on every refresh.

        Always 200: ``{ exists, content, mtime }``. ``exists=false`` (empty
        content, ``mtime=0``) for a task with no plan yet, no workspace, or
        any read error — the UI treats all of those the same.
        """
        from pathlib import Path

        from kato_core_lib.helpers.plan_writer import PLAN_FILENAME

        empty = {'exists': False, 'content': '', 'mtime': 0}
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify(empty)
        try:
            workspace_dir = workspace_manager.workspace_path(task_id)
        except Exception:  # noqa: BLE001 - unknown task → no plan
            return jsonify(empty)
        if not workspace_dir:
            return jsonify(empty)
        plan_path = Path(str(workspace_dir)) / PLAN_FILENAME
        try:
            if not plan_path.is_file():
                return jsonify(empty)
            content = plan_path.read_text(encoding='utf-8')
            mtime = plan_path.stat().st_mtime_ns
        except (OSError, ValueError, UnicodeDecodeError):
            return jsonify(empty)
        return jsonify({'exists': True, 'content': content, 'mtime': mtime})

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
        payload['context_usage'] = _session_context_usage(app, session, record)
        return jsonify(payload)

    @app.get('/api/sessions/<task_id>/context-usage')
    def get_session_context_usage(task_id: str):
        """Just the composer's context-window reading.

        Exists because the meter used to read it off ``GET /api/sessions/<id>``
        above and discard everything else — and "everything else" includes
        ``recent_events``, which is not a bounded tail. ``_recent_events`` in
        the streaming session is a plain list ("Memory grows linearly with
        events... A bounded deque was a footgun"), and each event's ``raw`` is
        the whole CLI stream-json object, tool inputs and outputs included.

        The meter refreshes on mount, on task switch, and at every TURN
        BOUNDARY — precisely when that log is longest — so the cost grew with
        the conversation and peaked exactly when the operator was waiting on
        the next turn. Four fields were paid for with the entire transcript.
        """
        manager = app.config['SESSION_MANAGER']
        record = manager.get_record(task_id)
        if record is None:
            return jsonify({'error': 'session not found'}), 404
        session = manager.get_session(task_id)
        return jsonify(_session_context_usage(app, session, record))

    @app.get('/api/claude/sessions')
    def list_claude_sessions():
        """List Claude Code sessions available for adoption.

        Reads ``~/.claude/projects/`` (or ``CLAUDE_SESSIONS_ROOT``
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
        # Each backend has its OWN chat history: the tabs are separate
        # conversations with different agents, so a Claude tab must never
        # list a Codex thread it cannot resume. Defaults to the active
        # backend, which is what the front tab is showing.
        from agent_core_lib.agent_core_lib.session.backend_chats import (
            parked_chat,
        )
        requested_backend = _requested_chat_backend(request.args) or str(
            getattr(record, 'agent_backend', '') or '',
        )
        chat = parked_chat(record, requested_backend)
        active_id = str(chat['agent_session_id'] or '')
        previous_ids = [
            sid for sid in chat['previous_session_ids']
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
                # Which CLI produced this chat. Read from the RECORD, not from
                # current config: the operator can switch backends between
                # chats, and yesterday's chat still belongs to the CLI that
                # wrote it. Empty for chats that predate the field — the UI
                # shows no chip rather than guessing.
                'agent_backend': requested_backend,
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
        # A fresh chat may pick a different backend. Recorded on the chat
        # (not read from config at spawn time) because that record is what
        # routes every later turn — and what tells the operator, months from
        # now, which CLI this conversation belongs to.
        chosen = _requested_chat_backend(payload)
        if chosen and not agent_session_id:
            record.agent_backend = chosen
            saver = getattr(manager, 'save_record', None)
            if callable(saver):
                saver(record)
        return jsonify({
            'task_id': record.task_id,
            AGENT_SESSION_ID: record.agent_session_id,
            'agent_backend': str(getattr(record, 'agent_backend', '') or ''),
            'previous_session_ids': list(record.previous_session_ids),
        })

    @app.post('/api/sessions/<task_id>/backend')
    def switch_task_backend(task_id: str):
        """Switch which agent this task's chat pane is talking to.

        The tabs are separate conversations with different agents. Switching
        parks the outgoing chat (id + history) and lifts the incoming one, so
        an operator who tries the other agent and comes back finds their
        thread where they left it rather than a blank one.

        The live subprocess of the outgoing chat is left running: the operator
        switched tabs, they did not end the conversation.
        """
        from agent_core_lib.agent_core_lib.session.backend_chats import (
            switch_backend,
        )
        backend = _requested_chat_backend(request.get_json(silent=True) or {})
        if not backend:
            return jsonify({'error': 'unknown agent backend'}), 400
        manager = app.config['SESSION_MANAGER']
        available = getattr(manager, 'available_backends', None)
        wired = list(available()) if callable(available) else []
        if wired and backend not in wired:
            # Switching to a backend with no manager would hand the operator
            # a tab whose first message fails.
            return jsonify({
                'error': f'the {backend} backend is not configured on this host',
            }), 400
        record = manager.get_record(task_id)
        if record is None:
            # No chat yet: nothing to park. The tab is still a valid place to
            # start one, so this is a no-op success rather than a 404.
            return jsonify({
                'task_id': task_id, 'agent_backend': backend,
                AGENT_SESSION_ID: '', 'previous_session_ids': [],
            })
        switch_backend(record, backend)
        saver = getattr(manager, 'save_record', None)
        if callable(saver):
            saver(record)
        return jsonify({
            'task_id': record.task_id,
            'agent_backend': record.agent_backend,
            AGENT_SESSION_ID: record.agent_session_id,
            'previous_session_ids': list(record.previous_session_ids),
        })

    @app.get('/api/sessions/<task_id>/agent-status')
    def get_task_agent_status(task_id: str):
        """Liveness of EVERY backend's chat for this task, not just the active one.

        Both subprocesses can run at once — switching agent tabs parks the
        outgoing conversation and leaves its process alive — so one status
        chip could only ever describe the tab in front of the operator, and
        said nothing about the agent still working behind it.

        The ACTIVE backend's chip is still driven by the live SSE stream in
        the UI (it alone distinguishes "sleeping" from "closed"); this answers
        for the ones the stream says nothing about.
        """
        from kato_core_lib.helpers.agent_backend_readiness import (
            CHAT_BACKENDS,
            backend_label,
        )
        manager = app.config.get('SESSION_MANAGER')
        lookup = getattr(manager, 'sessions_by_backend', None)
        sessions = {}
        if callable(lookup):
            try:
                sessions = lookup(task_id) or {}
            except Exception:
                app.logger.exception(
                    'per-backend session lookup failed for task %s', task_id,
                )
        active = ''
        resolver = getattr(manager, 'backend_for', None)
        if callable(resolver):
            try:
                active = str(resolver(task_id) or '').strip().lower()
            except Exception:
                active = ''
        rows = []
        for backend in CHAT_BACKENDS:
            if backend not in sessions:
                continue
            session = sessions.get(backend)
            rows.append({
                'id': backend,
                'label': backend_label(backend),
                'active': backend == active,
                'live': bool(getattr(session, 'is_alive', False)),
                'working': bool(getattr(session, 'is_working', False)),
            })
        return jsonify({'task_id': task_id, 'backends': rows})

    @app.get('/api/agent-backends')
    def list_agent_backends():
        """Backends this host can actually start a chat on.

        Derived from what is WIRED, not from a static list: offering a
        backend whose manager does not exist would hand the operator a
        picker entry that fails at the first message.
        """
        from kato_core_lib.helpers.agent_backend_readiness import (
            probe_chat_backends,
            reset_probe_cache,
        )
        if request.args.get('refresh'):
            # The setup panel's "Check again": an operator who just installed
            # the CLI must not have to wait out a cache they cannot see.
            reset_probe_cache()
        manager = app.config.get('SESSION_MANAGER')
        available = getattr(manager, 'available_backends', None)
        wired = set(available()) if callable(available) else set()
        if not wired:
            # A host with a single (unrouted) manager still has one backend.
            wired = {str(getattr(manager, 'AGENT_BACKEND', '') or '')}
        entries = []
        for probe in probe_chat_backends(app.config.get('AGENT_BINARIES') or {}):
            # ``wired`` and ``ready`` are DIFFERENT questions and both matter:
            # wired = kato built a session manager for it; ready = its CLI is
            # installed and answers. A tab is shown either way — an unready
            # one opens the setup panel instead of a chat — but only a
            # both-true backend can actually take a message.
            entries.append({
                **probe,
                'wired': probe['id'] in wired,
                'chat_available': probe['ready'] and probe['id'] in wired,
            })
        return jsonify({
            'backends': entries,
            'default': str(getattr(manager, 'default_backend', '') or ''),
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

    @app.get('/api/config-status')
    def config_status():
        """Is kato configured, and if not, WHAT is missing.

        Same source of truth as the boot-time check
        (``collect_config_errors``) so the two never drift — but evaluated
        across the layered settings stores (env > settings.json), so
        saving in the Settings UI clears the "not configured" state without a
        restart.

        - ``setup_mode``: did THIS process boot unconfigured? If so it is not
          scanning tickets and the UI shows the onboarding gate.
        - ``needs_config``: live config completeness. Flips to ``false`` the
          moment the operator finishes filling in the required settings.
        - ``missing``: the human-readable list of what's still required.
        """
        from kato_core_lib.helpers.kato_settings_store_utils import (
            effective_config_env,
        )
        from kato_core_lib.validate_env import collect_config_errors

        errors = collect_config_errors(mode='all', env=effective_config_env())
        return jsonify({
            'setup_mode': bool(app.config.get('NEEDS_CONFIG')),
            'needs_config': bool(errors),
            'missing': errors,
            # Why the last in-process start attempt failed ('' when none):
            # config can be COMPLETE yet still fail to start (bad token
            # fails connection validation). The wizard must show that
            # instead of "you're all set".
            'setup_error': str(app.config.get('SETUP_ERROR') or ''),
        })

    @app.get('/api/fs/dirs')
    def list_directories():
        """Directory listing for the folder-picker UI ("Browse…").

        The planning webserver runs on the operator's own machine, so
        browsing their filesystem here is equivalent to the terminal
        they'd otherwise use — and strictly less powerful: DIRECTORY
        NAMES only, never file names or contents. Hidden directories
        are skipped.
        """
        raw = (request.args.get('path') or '~').strip() or '~'
        try:
            base = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            return jsonify({'error': 'invalid path'}), 400
        if not base.is_dir():
            return jsonify({'error': f'not a directory: {base}'}), 404
        try:
            children = sorted(
                (
                    entry for entry in base.iterdir()
                    if entry.is_dir() and not entry.name.startswith('.')
                ),
                key=lambda entry: entry.name.lower(),
            )
        except PermissionError:
            return jsonify({'error': f'permission denied: {base}'}), 403
        parent = str(base.parent) if base.parent != base else None
        return jsonify({
            'path': str(base),
            'parent': parent,
            'home': str(Path.home()),
            'dirs': [
                {'name': entry.name, 'path': str(entry)} for entry in children
            ],
        })

    @app.get('/api/settings')
    def get_settings():
        """Operator-editable settings, resolved across kato's two stores.

        Source label tells the operator where the value currently
        lives: ``env`` (live process / shell) or ``kato_settings``
        (``~/.kato/settings.json`` — what the UI writes; kato's only
        config file).
        """
        from kato_core_lib.helpers.kato_settings_store_utils import (
            kato_settings_path,
        )

        repo_root = _resolve_setting('REPOSITORY_ROOT_PATH')
        return jsonify({
            'repository_root_path': repo_root,
            'settings_file_path': str(kato_settings_path()),
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
        """Shared GET shaping for the task / git provider routes: each
        field resolves via ``_resolve_setting`` (live env > settings.json).
        """
        out = {}
        for name, fields in fields_map.items():
            field_values = {key: _resolve_setting(key) for key in fields}
            out[name] = {'fields': field_values}
        return out

    @app.get('/api/credential-sources')
    def list_credential_sources():
        """Credentials for ``?provider=`` that are ALREADY on this machine.

        Powers the wizard's "use my gh CLI login" option so a first-comer
        never has to mint and paste a token — operator feedback was, in
        full, "api key is prehistoric". Probes the provider CLI, git's
        credential helper and the conventional env vars.

        Returns only ``{id, label, detail, account}`` per source — the
        token itself NEVER crosses to the browser; picking a source
        stores the source name, and kato resolves the live token
        server-side at boot.

        Unknown / non-discoverable providers (Jira, YouTrack: trackers
        with no CLI and no git credential) return an empty list, and the
        UI falls back to the paste form.
        """
        provider = str(request.args.get('provider', '') or '').strip().lower()
        if not provider:
            return jsonify({'error': 'provider is required'}), 400
        try:
            from kato_core_lib.helpers.credential_sources import (
                base_url_key,
                discover_credential_sources,
            )
            base_url = _resolve_setting(base_url_key(provider))['value']
            sources = discover_credential_sources(provider, base_url)
        except Exception:
            # A wedged CLI must not break the setup screen — the paste
            # form is always the fallback.
            app.logger.exception('credential source discovery failed')
            sources = []
        return jsonify({'provider': provider, 'sources': sources})

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

        out = _provider_field_values(_TASK_PROVIDER_FIELDS)
        active = (_resolve_setting('KATO_ISSUE_PLATFORM')['value'] or 'youtrack')
        active = active.strip().lower()
        return jsonify({
            'active': active,
            'providers': out,
            'settings_file_path': str(kato_settings_path()),
            'supported': list(_TASK_PROVIDER_FIELDS.keys()),
        })

    @app.post('/api/task-providers')
    def update_task_provider():
        """Persist one task platform's fields + active to settings.json.

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

        out = _provider_field_values(_GIT_HOST_FIELDS)
        return jsonify({
            'providers': out,
            'settings_file_path': str(kato_settings_path()),
            'supported': list(_GIT_HOST_FIELDS.keys()),
        })

    @app.post('/api/git-providers')
    def update_git_provider():
        """Persist one git host's credentials to settings.json.

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
            DEFAULT_ON_BOOL_KEYS,
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
                # Opt-OUT toggles are ON unless explicitly disabled — show them
                # checked when unset so they reflect the real default (no
                # ambiguous "off-looking but actually on" picker).
                if (field['key'] in DEFAULT_ON_BOOL_KEYS
                        and not str(field['value']).strip()):
                    field['value'] = 'true'
                    field['source'] = 'default'
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
        numbers are coerced to the string form settings.json stores.
        ``restart_required`` because kato reads env at boot.
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
        return _validate_persist_and_respond(updates, app)

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
        recheck = getattr(getattr(agent_service, 'publish', agent_service), 'recheck_repository_push_access', None)
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
        # A merge the agent has just resolved is still uncommitted (the agent
        # can't run git), so the tree would show every merged-in file as a
        # conflict + change. Finalise it here — a cheap no-op unless a merge
        # is pending AND its markers are gone — so the tree reflects only the
        # branch's work.
        _finalize_resolved_merges(agent_service, task_id)
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
            # The task folder's OWN files, after the repo clones. The agent
            # writes real deliverables here (a scratch page to open in a
            # browser, pr_description.md) and nothing listed them, so the
            # operator could not reach a file kato had just told them about.
            task_root = _task_workspace_root(workspace_manager, task_id)
            if task_root:
                own_files = task_folder_file_tree(
                    task_root, [t['cwd'] for t in trees],
                )
                if own_files:
                    trees.append({
                        'repo_id': TASK_FOLDER_TREE_ID,
                        'cwd': task_root,
                        'read_only': True,
                        'tree': own_files,
                        # Not a git repo — no diff base, so nothing to
                        # colour and no conflicts to mark.
                        'conflicted_files': [],
                        'changed_files': [],
                        # ...and therefore nothing can ever be "changed" here.
                        # Without this the section rendered the default
                        # changed-files view, which for an empty changed set
                        # says "Nothing changed yet" — so plan.md and
                        # pr_description.md, real deliverables the agent had
                        # just told the operator about, were listed by this
                        # endpoint and never shown.
                        'has_diff': False,
                    })
            if trees:
                return jsonify({
                    'repository_ids': [
                        t['repo_id'] for t in trees
                        if t['repo_id'] != TASK_FOLDER_TREE_ID
                    ],
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
        # Commit a just-resolved merge before diffing so the Changes tab
        # shows only the branch's work, not the whole merged-in default
        # branch (see list_session_files). No-op unless a merge is pending.
        _finalize_resolved_merges(agent_service, task_id)
        # ``?full=<path>`` (repeatable) opts ONE file out of the oversized-diff
        # elision. Per-path on purpose: a blanket "send everything" would let a
        # 40-file changeset undo the protection the cap exists for.
        full_paths = tuple(request.args.getlist('full'))
        # ``?repo=<id>`` scopes the response to ONE clone.
        #
        # The client has been sending it all along and the handler ignored it,
        # so de-eliding a single file re-ran ``_compute_repo_diff`` for EVERY
        # repository in the task — each one a fresh set of git subprocesses,
        # nothing cached — and shipped the lot, for the client to pick one
        # entry out of with a ``.find`` and discard the rest. On a multi-repo
        # task that is the whole changeset recomputed to open one file.
        #
        # An unknown id yields an EMPTY result rather than falling back to
        # every repo: silently widening a scoped request is how a filter
        # becomes a no-op nobody notices.
        wanted_repo = str(request.args.get('repo', '') or '').strip()
        workspace_status = _workspace_status(workspace_manager, task_id)
        repository_ids = _task_repository_ids(workspace_manager, task_id)
        # Whether this task has ENUMERATED repos at all, recorded before the
        # filter empties the list — the legacy single-repo path below keys off
        # it, and a scoped miss must not be confused with "no repos".
        has_enumerated_repos = bool(repository_ids)
        if wanted_repo and repository_ids:
            repository_ids = [
                repo_id for repo_id in repository_ids
                if str(repo_id) == wanted_repo
            ]
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
                    full_paths=full_paths,
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
        # Did a scoped request miss?
        #
        # A scope that misses must NOT fall through to the legacy whole-record
        # path — answering "just this repo" with a different repo's diff is
        # worse than answering nothing.
        #
        # But the legacy path has a repo identity of its own, even though the
        # payload reports ``repo_id: ''``: the client synthesizes one from the
        # cwd's basename (``diffModel.js``) and sends THAT back on ``?repo``
        # when de-eliding a file. Treating every scoped request as a miss here
        # broke the only way to read an oversized file's diff — the request
        # answered empty and the pane reported the file missing. So compare
        # against the same name the client derived, rather than either
        # rejecting all of them or serving any of them.
        legacy_repo_id = os.path.basename(str(cwd or '').rstrip('/\\'))
        scoped_miss = bool(wanted_repo) and (
            has_enumerated_repos or wanted_repo != legacy_repo_id
        )
        if cwd is None or scoped_miss:
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
        single = _compute_repo_diff(
            '', cwd, task_id=task_id, agent_service=agent_service,
            full_paths=full_paths,
        )
        return jsonify({
            'repository_ids': [],
            'diffs': [single],
            'workspace_status': workspace_status,
            'repo_id': '',
            'base': single['base'],
            'head': single['head'],
            'diff': single['diff'],
        })

    @app.post('/api/sessions/<task_id>/files/discard-changes')
    def discard_session_file_changes(task_id: str):
        """Discard uncommitted changes to one file in a task clone.

        Backs the Files-tree right-click "Discard changes" — named for the
        effect, not for a git subcommand: "revert" would read as ``git
        revert`` (a NEW commit undoing an old one), which is a different
        operation. This runs ``git restore``. Deliberately an
        operator-driven git call rather than a message to the agent: it
        works with no session running, costs no turn, and cannot be
        reinterpreted by a model.

        Body: ``{"repo_id": "...", "path": "<repo-relative path>"}``.

        The path is validated twice on purpose — here against the task's
        own workspace roots (so nothing outside the task can be named at
        all), and again in ``GitClientMixin.restore_paths``, which refuses
        anything that is not a plain repo-relative file path. This
        DESTROYS uncommitted work, and the work is not committed until
        publish, so there is nothing to recover it from.
        """
        body = request.get_json(silent=True) or {}
        repo_id = str(body.get('repo_id', '') or '').strip()
        raw_path = str(body.get('path', '') or '').strip()
        if not raw_path:
            return jsonify({'error': 'path is required'}), 400
        service = app.config.get('AGENT_SERVICE')
        discard = _agent_method(
            service, 'repositories.discard_workspace_file_changes',
        ) if service is not None else None
        if not callable(discard):
            return jsonify({'error': 'not available'}), 503
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        repo_ids = _task_repository_ids(workspace_manager, task_id)
        if repo_id and repo_id not in repo_ids:
            return jsonify({'error': f'unknown repository {repo_id}'}), 404
        # No repo named (older callers / a flat tree): find the clone the
        # path actually lives in rather than guessing the first one.
        candidates = [repo_id] if repo_id else list(repo_ids)
        from pathlib import Path
        for candidate in candidates:
            cwd = _repository_cwd(workspace_manager, task_id, candidate)
            if not cwd:
                continue
            try:
                resolved = (Path(cwd) / raw_path).resolve()
                root = Path(cwd).resolve()
            except (OSError, ValueError):
                continue
            if not _is_inside(resolved, root):
                return jsonify({'error': 'path is outside the task workspace'}), 403
            if not repo_id and not resolved.exists():
                continue
            # Anchor the discard on the SAME ref the Files tree colours
            # against. Against HEAD, a change the agent had already committed
            # on the task branch looked clean — the operator clicked Discard
            # and watched nothing happen while the file stayed marked.
            base = _resolve_diff_base(candidate, cwd, service)
            base_ref, _is_local = resolve_base_ref(cwd, base)
            try:
                discarded = discard(
                    task_id, candidate, [raw_path], source=base_ref,
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            except Exception as exc:
                return jsonify({'error': f'discard failed: {exc}'}), 500
            return jsonify({'discarded': discarded, 'repo_id': candidate})
        return jsonify({'error': 'file not found in this task workspace'}), 404

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
        # The TASK FOLDER itself, not just the repo clones inside it. The
        # agent writes real deliverables there — a scratch HTML page to try
        # something in a browser, pr_description.md, plan.md — and the
        # operator opening one got "path is outside the task workspace",
        # which reads as kato refusing to show a file it just told them it
        # created. It is the task's own folder, so it is in scope by
        # definition; ``_task_workspace_root`` never derives it by walking
        # up from a repo path, so this cannot widen to the operator's whole
        # source root.
        task_root = _task_workspace_root(workspace_manager, task_id)
        if task_root:
            roots.append(task_root)
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
            stat_result = resolved.stat()
        except OSError as exc:
            return jsonify({'error': f'stat failed: {exc}'}), 500
        size = stat_result.st_size
        # Stable string form (not the raw float) so the client can send
        # it straight back for an EXACT match — see below. The client
        # caches file content across task-tab switches; a background
        # branch sync, merge, or an operator editing the file directly
        # can all change it without any SSE event the frontend would
        # ever see, so re-verifying against the server's OWN stat() on
        # every read (not a client-side "has anything happened?" guess)
        # is what makes that cache safe rather than a staleness risk.
        mtime = str(stat_result.st_mtime)
        known_mtime = (request.args.get('known_mtime') or '').strip()
        if known_mtime and known_mtime == mtime:
            return jsonify({
                'path': str(resolved),
                'size': size,
                'mtime': mtime,
                'unchanged': True,
            })
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
                'mtime': mtime,
                'binary': True,
            })
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('utf-8', errors='replace')
        return jsonify({
            'path': str(resolved),
            'size': size,
            'mtime': mtime,
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
        ref, is_local = resolve_base_ref(cwd, base)
        if is_local and has_origin_remote(cwd):
            return jsonify({'error': _no_base_error_message(repo_id)}), 404
        # For a no-remote local clone ``ref`` is HEAD — the base the diff is
        # computed against — so context expansion reads the right blob.
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
        ref, is_local = resolve_base_ref(cwd, base)
        if is_local and has_origin_remote(cwd):
            return jsonify({
                'commits': [],
                'error': _no_base_error_message(repo_id),
            }), 200
        # No-remote local clone: no "commits ahead of a base" concept, so the
        # list is simply empty (HEAD ahead of HEAD is nothing) — not an error.
        commits = list_branch_commits(cwd, ref, limit=limit)
        return jsonify({
            'repo_id': repo_id,
            'base': '' if is_local else base,
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
            app, 'publish.approve_push',
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
        check = getattr(getattr(agent_service, 'publish', agent_service), 'is_awaiting_push_approval', None)
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
            app, 'publish.push_task',
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
            app, 'publish.pull_task',
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
            app, 'publish.merge_default_branch_for_task',
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
            app, 'publish.create_pull_request_for_task',
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
            app, 'publish.update_source_for_task',
            not_callable_message='agent service does not support source-update',
        )
        if err:
            return err
        return _envelope_response(update(task_id), 'updated')

    @app.get('/api/sessions/<task_id>/comments')
    def list_task_comments(task_id: str):
        """Every comment on the task workspace (optionally per-repo).

        ``?repo`` works — it reaches ``comment_store.list_for_repo``, which has
        its own tests including case-insensitive matching — but NO client sends
        it, and that is deliberate rather than an oversight. One unscoped fetch
        feeds the file-tree badges, the diff-pane threads, the editor threads
        and the chat tint (``stores/taskCache/slices/commentsChild.js``);
        narrowing it would make four surfaces each ask for their own slice.
        ``DiffPane.test.jsx`` pins the unscoped call.

        Kept because it is a working, tested capability rather than rot — the
        same call made for ``sync_remote_comments`` above. The dead half, a
        ``repoId`` parameter on the client function that the one call site
        never passed, is gone.
        """
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({'error': 'agent service not wired'}), 503
        list_comments = getattr(
        getattr(agent_service, 'comments', agent_service), 'list_task_comments', None)
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
            app, 'comments.add_task_comment',
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
            app, 'comments.resolve_task_comment',
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
            app, 'comments.mark_comment_addressed',
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
            app, 'comments.reopen_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        return jsonify(reopen(task_id, comment_id))

    @app.post('/api/sessions/<task_id>/comments/<comment_id>/retry')
    def retry_task_comment(task_id: str, comment_id: str):
        retry, err = _resolve_agent_method(
            app, 'comments.retry_task_comment',
            not_callable_message='comments not supported',
        )
        if err:
            return err
        return jsonify(retry(task_id, comment_id))

    @app.delete('/api/sessions/<task_id>/comments/<comment_id>')
    def delete_task_comment(task_id: str, comment_id: str):
        delete, err = _resolve_agent_method(
            app, 'comments.delete_task_comment',
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
            app, 'comments.edit_task_comment',
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
        """Pull remote PR comments + ``git pull`` the workspace clone.

        BUILT BUT NOT WIRED. No client calls this: ``api.js`` has no
        ``syncTaskComments`` export, and the Changes tab's sync icon calls
        ``syncTaskRepositories`` — a different operation. An audit flagged the
        pair as dead and proposed deleting it along with
        ``task_comment_service.sync_remote_comments``.

        Kept deliberately. The backing method is a working, ~15-test-covered
        capability (it upserts PROVIDER comments into the same local store the
        agent reads, which ``test_comment_prompt_framing_parity`` reasons about
        as an invariant), so deleting it destroys built work rather than
        removing rot. Whether to wire it to a control or drop it is a product
        call, not a cleanup — flagged for the operator rather than decided here.
        """
        sync, err = _resolve_agent_method(
            app, 'comments.sync_remote_comments',
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
        list_repos = _agent_method(
            agent_service, 'repositories.list_inventory_repositories',
        )
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
            app, 'repositories.add_task_repository',
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
            app, 'repositories.sync_task_repositories',
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
            app, 'publish.finish_task_planning_session',
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
        """Git-button state (workspace + local push-readiness). Local-only
        and instant — the PR-existence check lives at ``/pull-request-state``
        so a slow provider can never freeze these buttons."""
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is None:
            return jsonify({
                'has_workspace': False,
                'has_changes_to_push': False,
                'task_id': task_id,
            })
        check = getattr(getattr(agent_service, 'publish', agent_service), 'task_publish_state', None)
        if not callable(check):
            return jsonify({
                'has_workspace': False,
                'has_changes_to_push': False,
                'task_id': task_id,
            })
        state = check(task_id) or {}
        state['task_id'] = task_id
        return jsonify(state)

    @app.get('/api/sessions/<task_id>/pull-request-state')
    def get_task_pull_request_state(task_id: str):
        """Best-effort PR-existence for the Pull-request button + "open PR"
        link. Fetched separately from publish-state (tab-load + click, not
        polled) so its provider retry backoff never blocks the git buttons."""
        agent_service = app.config.get('AGENT_SERVICE')
        check = getattr(getattr(agent_service, 'publish', agent_service), 'task_pull_request_state', None)
        if not callable(check):
            return jsonify({
                'has_pull_request': False,
                'pull_request_urls': [],
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
        search = _agent_method(agent_service, 'repositories.search_task_workspace')
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

        ``?done=1`` (the forget dialog's "this task is done" checkbox)
        additionally moves the TICKET to the tracker's done column
        before any of that runs — see ``_move_task_to_done_or_error``
        for why that ordering is the safe one.
        """
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        if workspace_manager is None:
            return jsonify({'error': 'workspace manager not wired'}), 503
        mark_done = _truthy_arg(request.args.get('done'))
        if mark_done:
            done_error = _move_task_to_done_or_error(app, task_id)
            if done_error:
                return jsonify({
                    'forgotten': False,
                    'moved_to_done': False,
                    'task_id': task_id,
                    'error': done_error,
                }), 502
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
        # Drop any persisted plan-mode lock + its live override so a
        # forgotten task doesn't reappear plan-locked after a restart.
        from kato_core_lib.helpers.plan_mode_store import set_plan_mode
        set_plan_mode(task_id, False)
        _set_task_override(app, 'TASK_PLAN_MODE_OVERRIDES', task_id, '')
        # Same for Remote Control: a forgotten task must not come back
        # bridged to the Claude app the next time it is adopted.
        _store_remote_control(app, task_id, False)
        # Drop the task's registry state — PR contexts AND its persisted
        # processed-review-comment marks — so ~/.kato/processed_review_comments.json
        # never keeps marks for a task that no longer exists. Best-effort: this
        # secondary cleanup never blocks the workspace/session removal that IS the
        # point of DELETE (and the task is already marked forgotten above).
        agent_service = app.config.get('AGENT_SERVICE')
        if agent_service is not None:
            try:
                agent_service.forget_task_state(task_id)
            except Exception:
                app.logger.warning(
                    'failed to drop registry state for deleted task %s '
                    '(workspace/session removal still applies)', task_id,
                )
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
                'moved_to_done': mark_done,
                'task_id': task_id,
                'error': '; '.join(errors),
            }), 500
        return jsonify({
            'forgotten': True,
            'moved_to_done': mark_done,
            'task_id': task_id,
        })


# ----- live status feed (SSE) -----


def _register_status_routes(app: Flask) -> None:

    # NOTE: no ``/api/status/recent``. It served ``broadcaster.recent()`` as a
    # one-shot poll, which ``/api/status/events`` below already ships as the
    # connect backlog before it starts streaming — and the SSE stream is what
    # the UI actually opens (hooks/useStatusFeed.js). Nothing called the poll.
    # It read as live because the module header above listed it.

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
    _register_tool_decisions_routes(app)
    _register_action_guard_audit_route(app)
    _register_agent_version_route(app)
    _register_agent_version_upgrade_route(app)


def _register_session_events_route(app: Flask) -> None:
    @app.get('/api/sessions/<task_id>/events')
    def session_events_stream(task_id: str):
        manager = app.config['SESSION_MANAGER']
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        agent_service = app.config.get('AGENT_SERVICE')
        return _sse_response(
            _event_stream_generator(
                manager, workspace_manager, task_id, agent_service, app=app,
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


# ----- Remote Control (composer ``/`` menu, Claude only) -----
#
# Claude Code can hand a session it is already running to claude.ai / the
# Claude app, so the operator can pick the same conversation up from another
# device. Kato exposes that per task, because kato runs many sessions at once
# and "expose all of them" is a much bigger statement than the operator makes
# when they want to follow ONE task from their phone.
#
# It is not a spawn flag. The CLI takes it as a control request on the live
# subprocess (``claude_core_lib/helpers/remote_control.py``), which is why
# these helpers talk to the session object rather than to the spawn argv.


def _remote_control_backend(app: Flask, task_id: str) -> str:
    """The chat backend a task is bound to ('' when unknown)."""
    manager = app.config.get('SESSION_MANAGER')
    resolver = getattr(manager, 'backend_for', None)
    if not callable(resolver):
        return str(getattr(manager, 'AGENT_BACKEND', '') or '').strip().lower()
    try:
        return str(resolver(task_id) or '').strip().lower()
    except Exception:
        return ''


def _remote_control_supported(app: Flask, task_id: str) -> bool:
    """Whether this task's agent can be remote-controlled at all.

    Two conditions, both required: the task is on Claude (Codex and OpenHands
    have no equivalent), and the installed Claude CLI advertises the feature.
    The second is probed from ``--help`` rather than compared against a
    version floor — see ``supports_remote_control``.
    """
    from claude_core_lib.claude_core_lib.helpers.remote_control import (
        supports_remote_control,
    )
    backend = _remote_control_backend(app, task_id)
    if backend and backend != 'claude':
        return False
    return supports_remote_control(_claude_binary(app))


def _claude_binary(app: Flask) -> str:
    """The CLAUDE binary to probe — not whatever the default runner holds.

    On a multi-backend host the planning runner's ``binary`` is one backend's
    CLI, so a Claude task on a Codex-default host would have been probed with
    ``codex --help``, found no ``--remote-control``, and reported the feature
    unsupported on exactly the tab that can use it. ``AGENT_BINARIES`` is the
    per-backend map the readiness probe already keys on.
    """
    configured = str((app.config.get('AGENT_BINARIES') or {}).get('claude', '') or '')
    if configured.strip():
        return configured.strip()
    defaults = _chat_runner_defaults(app)
    binary = str(getattr(defaults, 'binary', '') or '') if defaults else ''
    # The default runner's binary is only usable when it IS the claude one.
    return binary.strip() if 'claude' in binary.lower() else 'claude'


def _remote_control_status(app: Flask, task_id: str) -> dict:
    store = app.config.get('TASK_REMOTE_CONTROL')
    enabled = bool(store is not None and task_id in store)
    live = {'enabled': False, 'session_url': '', 'connect_url': ''}
    manager = app.config.get('SESSION_MANAGER')
    if manager is not None:
        try:
            session = manager.get_session(task_id)
        except Exception:
            session = None
        state = getattr(session, 'remote_control', None) if session else None
        if isinstance(state, dict):
            live = state
    return {
        'supported': _remote_control_supported(app, task_id),
        'enabled': enabled,
        'live': bool(live.get('enabled')),
        'session_url': str(live.get('session_url', '') or ''),
        'connect_url': str(live.get('connect_url', '') or ''),
    }


def _store_remote_control(app: Flask, task_id: str, on: bool) -> None:
    store = app.config.get('TASK_REMOTE_CONTROL')
    if store is None:
        return
    if on:
        store.add(task_id)
    else:
        store.discard(task_id)
    # Best-effort persistence — a write failure must not fail the toggle the
    # operator just made on the live session.
    from kato_core_lib.helpers.remote_control_store import set_remote_control_enabled
    try:
        set_remote_control_enabled(task_id, on)
    except Exception:
        app.logger.warning(
            'task %s: could not persist the remote control preference', task_id,
        )


def _apply_remote_control(app: Flask, task_id: str, on: bool) -> tuple[bool, str]:
    """Push the toggle to the live subprocess. ``(applied, error_message)``.

    Synchronous, unlike the spawn-time path: the operator clicked the switch
    and is owed both the URL and any refusal. No live session is not an
    error — the preference is stored and the next spawn applies it, which is
    the common case for a tab nobody has typed in for a while.
    """
    from kato_core_lib.helpers.remote_control_store import apply_remote_control

    manager = app.config.get('SESSION_MANAGER')
    if manager is None:
        return False, ''
    try:
        session = manager.get_session(task_id)
    except Exception:
        session = None
    try:
        applied = apply_remote_control(session, task_id, on)
    except Exception as exc:
        app.logger.warning(
            'task %s: remote control %s failed: %s',
            task_id, 'enable' if on else 'disable', exc,
        )
        return False, str(exc)
    return bool(applied), ''


def _discover_chat_effort_levels(app: Flask) -> list:
    """Effort levels the chat CLI advertises (discovered, with fallback)."""
    from agent_backend_core_lib.agent_backend_core_lib.client.model_catalog_factory import (
        discover_effort_levels,
        platform_for_binary,
    )
    defaults = _chat_runner_defaults(app)
    binary = str(getattr(defaults, 'binary', '') or 'claude') if defaults else 'claude'
    return list(discover_effort_levels(platform_for_binary(binary), binary))


def _truthy_arg(value: object) -> bool:
    """Whether a query-string flag (e.g. ``?refresh=1``) is set/affirmative."""
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _move_task_to_done_or_error(app: Flask, task_id: str) -> str:
    """Move ``task_id``'s ticket to done. Returns '' on success, else why not.

    Called BEFORE the forget endpoint destroys anything, and a failure
    aborts the whole delete. The ticket move is the only half of this
    operation that can't be redone from the UI afterwards — once the
    clone and the session record are gone the tab is gone with them, so
    an operator who saw "couldn't move to done" would have nothing left
    to retry from. Failing first leaves everything intact: they retry,
    or uncheck the box and delete locally anyway.
    """
    agent_service = app.config.get('AGENT_SERVICE')
    mark_done = getattr(agent_service, 'mark_task_done', None)
    if not callable(mark_done):
        return (
            'kato is not connected to a task platform, so '
            f'{task_id} cannot be moved to done (nothing was deleted)'
        )
    try:
        mark_done(task_id)
    except Exception as exc:
        app.logger.exception('failed to move task %s to done', task_id)
        reason = str(exc) or exc.__class__.__name__
        return (
            f'could not move {task_id} to done on the task tracker: '
            f'{reason} (nothing was deleted)'
        )
    return ''


def _discover_chat_models(app: Flask, force: bool = False) -> list:
    """Models the chat backend offers (discovered, with fallback).

    Never hardcodes a version. Claude serves the stable CLI aliases
    (opus/sonnet/haiku) — which always resolve to the latest — with labels
    enriched by the live Anthropic models API when a credential is configured;
    Codex reads the codex CLI's own model cache. Any failure falls back to a
    sane static set so the picker always renders. ``force`` bypasses the
    discovery TTL cache so a refresh picks up a just-installed CLI's labels
    without waiting out the cache (or restarting kato).
    """
    from agent_backend_core_lib.agent_backend_core_lib.client.model_catalog_factory import (
        discover_models,
        platform_for_binary,
    )
    defaults = _chat_runner_defaults(app)
    binary = str(getattr(defaults, 'binary', '') or 'claude') if defaults else 'claude'
    # WHICH backend answers is the factory lib's question, not this route's:
    # a ``'codex' in binary`` test here is a backend switch in the UI layer,
    # and it silently sends every unknown backend down the Claude path.
    models = discover_models(platform_for_binary(binary), force=force)
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
    ``opus`` alias the picker offers. Family-level matching is truthful for
    EVERY family — including fable — because the picker now offers the stable
    CLI alias, which resolves to the latest of that family, rather than a
    pinned full id that could name a different concrete model than the one
    spawn would run.
    """
    candidate = (configured or '').strip().lower()
    if candidate in ids:
        return candidate
    for family in ('fable', 'opus', 'sonnet', 'haiku'):
        if family in ids and (candidate == family or candidate.startswith(f'claude-{family}')):
            return family
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
    def _log_message_routing(app, manager, task_id, payload):
        """Record the tab the caller named and the backend that will run it."""
        try:
            asked = _requested_chat_backend(payload) or '(none)'
            resolver = getattr(manager, 'backend_for', None)
            resolved = (
                str(resolver(task_id) or '') if callable(resolver) else '(unrouted)'
            )
            app.logger.info(
                'task %s: chat message from the %s tab → running on %s',
                task_id, asked, resolved or '(default)',
            )
        except Exception:
            # Instrumentation must never be able to fail a message send.
            app.logger.exception('could not log message routing for %s', task_id)

    def _align_backend_with_caller(app, manager, task_id, payload):
        """Make the record agree with the tab the message was typed into.

        A no-op when the caller names no backend (an older UI, or any
        non-chat caller) or when it already matches. Never raises: a chat
        message must not fail because the record could not be re-pointed.
        """
        wanted = _requested_chat_backend(payload)
        if not wanted:
            return
        resolver = getattr(manager, 'backend_for', None)
        if not callable(resolver):
            return
        try:
            if str(resolver(task_id) or '').strip().lower() == wanted:
                return
            available = getattr(manager, 'available_backends', None)
            wired = list(available()) if callable(available) else []
            if wired and wanted not in wired:
                return
            record = manager.get_record(task_id)
            if record is None:
                return
            from agent_core_lib.agent_core_lib.session.backend_chats import (
                switch_backend,
            )
            switch_backend(record, wanted)
            saver = getattr(manager, 'save_record', None)
            if callable(saver):
                saver(record)
            app.logger.info(
                'task %s: message sent from the %s tab; record re-pointed',
                task_id, wanted,
            )
        except Exception:
            app.logger.exception(
                'could not align the backend with the caller for task %s', task_id,
            )

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
        # The TAB the operator typed into is authoritative. The backend used
        # to be re-derived from the record alone, so a UI whose session poll
        # had not caught up could send from the Claude tab into a record that
        # said Codex — and kato launched the wrong CLI (reported on Windows
        # as "failed to launch codex: [WinError 2]" from the CLAUDE tab).
        _align_backend_with_caller(app, manager, task_id, payload)
        # WHICH agent this message will actually run on, every time. Routing
        # is decided across the browser, the record and the router, and when
        # an operator reports "it went to the wrong agent" there was no way
        # to tell where the disagreement was — only guesses. One line in the
        # activity log settles it.
        _log_message_routing(app, manager, task_id, payload)
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
            or _plan_mode_change_needs_respawn(app, manager, task_id, images)
            or _tool_floor_change_needs_respawn(app, manager, task_id, images)
        ):
            try:
                manager.terminate_session(task_id, remove_record=False)
            except Exception:
                app.logger.exception(
                    'failed to terminate session for model/effort/plan respawn (task %s)',
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
    capture = _agent_method(service, 'lessons.capture_prompt_lesson_candidate')
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


def _resolve_permission_decision(
    app: Flask,
    session,
    task_id: str,
    request_id: str,
    allow: bool,
    rationale: str,
    *,
    hook_tool_name: str = '',
) -> dict:
    """Core permission-resolution logic: re-derive the tool SERVER-SIDE
    (never trust a caller's claimed command), classify it via Action
    Guard, run the operator's ``pre_tool_use`` hook, deliver the
    decision, then audit + ``post_tool_use``.

    Shared by the operator-facing ``POST /permission`` route AND the
    server-side auto-resolve path for a remembered decision
    (``_maybe_auto_resolve_pending``) — routing both through the same
    function guarantees a remembered "allow" gets IDENTICAL enforcement
    to a human clicking Allow, never a shortcut around either check.

    Returns ``{'status': 'delivered', 'allow': ...}`` on success, or
    ``{'error': ...}`` if delivering the decision to the session failed.
    """
    # The operator's ORIGINAL intent, before a hard-block override flips it
    # below. Used to word the feed bubble correctly: "you approved, but this
    # is a hard floor" reads very differently from "you denied this".
    operator_approved = allow
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
        blocked, hook_rationale = _run_pre_tool_use_hook(app, task_id, {
            'request_id': request_id, 'tool': hook_tool_name, 'allow': allow,
        })
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
        return {'error': str(exc)}
    # A hard BLOCK becomes a loud bubble in the feed; every risky decision
    # (block / approved-ask / denied-ask) is recorded to the audit log.
    if guard_blocked:
        _publish_action_guard_block(
            session, verdict, operator_approved=operator_approved,
        )
    _audit_action_guard(app, task_id, request_id, verdict, guard_command, allow)
    # ``post_tool_use`` sees the final, post-hook decision so the
    # audit log reflects what actually got delivered to Claude.
    _fire_webserver_hook(app, 'post_tool_use', {
        'task_id': task_id,
        'request_id': request_id,
        'allow': bool(allow),
        'rationale': rationale,
        'tool': hook_tool_name,
        'action_guard_category': (
            _action_guard_enum_value(verdict.category) if verdict else ''
        ),
    })
    return {'status': 'delivered', 'allow': allow}


def _remember_decision_for_pending(session, request_id: str, allow: bool) -> None:
    """Persist an "Allow always" / "Deny always" choice server-side.

    Never remembers an ``AskUserQuestion``-shaped ask — each question is
    a distinct clarification, never a repeat of a previously-approved
    action (mirrors the removed client-side carve-out).
    """
    from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
    from kato_core_lib.helpers.tool_decision_utils import (
        decision_command_for,
        is_answerable_question,
    )
    tool_name, tool_input = _pending_tool(session, request_id)
    if not tool_name or is_answerable_question(tool_input):
        return
    remember_tool_decision(
        tool_name, decision_command_for(tool_name, tool_input), allow,
    )


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
        if bool(payload.get('remember', False)):
            _remember_decision_for_pending(session, request_id, allow)
        result = _resolve_permission_decision(
            app, session, task_id, request_id, allow, rationale,
            hook_tool_name=str(payload.get('tool', '') or ''),
        )
        if 'error' in result:
            return jsonify(result), 500
        return jsonify(result)


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


def _context_usage_from_record(record, app: Flask | None = None) -> dict:
    """Last persisted context reading for a task (zeros when there is none).

    The live figure lives on the SUBPROCESS, so a sleeping session — or any
    session after a host restart — had nothing to report and the composer's
    indicator disappeared entirely. The record keeps the last value a live
    turn produced so the reading outlives the process that measured it.
    """
    empty = {'used_tokens': 0, 'limit_tokens': 0, 'model': '', 'baseline_tokens': 0}
    if record is None:
        return empty
    try:
        used = int(getattr(record, 'context_used_tokens', 0) or 0)
        model = str(getattr(record, 'context_model', '') or '')
        baseline = int(getattr(record, 'context_baseline_tokens', 0) or 0)
    except (TypeError, ValueError):
        return empty
    if used <= 0 or not model:
        return empty
    # A chat with a reading but no floor: recover it from the transcript and
    # keep it. Doing this ONLY on the live-session path (where the reading is
    # persisted) left a SLEEPING chat blank forever — and a sleeping chat is
    # exactly the one nobody is watching, which is who this indicator is for.
    if baseline <= 0 and app is not None:
        baseline = _ensure_recovered_baseline(app, record)
    return {
        'used_tokens': used,
        'limit_tokens': widen_window_to_observed(
            context_window_tokens(model), used,
        ),
        'model': model,
        # What this chat cost on its first measured turn. The UI divides
        # ``used`` by it to say how much more this session costs than
        # starting a fresh one — the signal the "% left" meter can't give:
        # a session at 490k of a 1M window looks half empty while charging
        # 490k on every single turn.
        'baseline_tokens': baseline,
    }


def _session_context_usage(app: Flask, session, record=None) -> dict:
    """Context-window usage for the composer indicator.

    Always the same shape so the UI has one branch, not three:
    ``{used_tokens, limit_tokens, model, baseline_tokens}``. Falls back to the last PERSISTED
    reading when there is no live subprocess, so the indicator survives a
    sleeping session instead of blinking out between turns. Zeros only when
    nothing has ever been measured, which the UI renders as "unknown" —
    never as 0% used, which would read as "plenty of room".
    """
    if session is None:
        return _context_usage_from_record(record, app)
    reader = getattr(session, 'context_usage', None)
    if not callable(reader):
        return _context_usage_from_record(record, app)
    try:
        usage = reader()
    except Exception:
        app.logger.exception('failed to read session context usage')
        return _context_usage_from_record(record, app)
    if not isinstance(usage, dict):
        return _context_usage_from_record(record, app)
    # A live session that has not seen an assistant turn YET (just spawned,
    # or resumed after a restart) reports zeros — fall back rather than blank
    # the indicator, then persist any real reading so it outlives this
    # subprocess.
    if int(usage.get('used_tokens', 0) or 0) <= 0:
        return _context_usage_from_record(record, app)
    _persist_context_usage(app, record, usage)
    # The baseline lives on the record (the session object only knows the
    # current turn), so stamp it onto the live reading the UI receives.
    usage = dict(usage)
    usage['baseline_tokens'] = int(
        getattr(record, 'context_baseline_tokens', 0) or 0,
    )
    return usage


# What a FRESH chat can plausibly cost on its first turn: system prompt +
# project instructions + whatever docs are injected. Comfortably above any
# real floor (those run to tens of thousands of tokens) and far below a
# conversation already in progress. A genuinely huge first turn simply gets
# no cost reading, which is the safe direction.
_MAX_PLAUSIBLE_BASELINE_TOKENS = 150_000


def _ensure_recovered_baseline(app: Flask, record) -> int:
    """Recover a missing floor from the transcript and persist it (or 0)."""
    recovered = _recovered_chat_floor(app, record)
    if recovered <= 0:
        return 0
    try:
        record.context_baseline_tokens = recovered
        manager = app.config.get('SESSION_MANAGER')
        saver = getattr(manager, 'save_record', None) or getattr(
            manager, 'update_record', None,
        )
        if callable(saver):
            saver(record)
    except Exception:
        # Not persisting just means we recover it again next time.
        app.logger.exception('failed to persist a recovered chat floor')
    return recovered


def _recovered_chat_floor(app: Flask, record) -> int:
    """First-turn context size for an in-progress chat, from its transcript."""
    try:
        from claude_core_lib.claude_core_lib.helpers.chat_floor import (
            chat_floor_tokens,
        )
        return chat_floor_tokens(
            str(getattr(record, 'agent_session_id', '') or ''),
            str(getattr(record, 'cwd', '') or ''),
        )
    except Exception:
        # Best-effort by design: the indicator hides itself when the floor
        # is unknown, which is the safe direction.
        app.logger.exception('failed to recover the chat floor from a transcript')
        return 0


def _persist_context_usage(app: Flask, record, usage: dict) -> None:
    """Mirror a live reading onto the record so it survives the subprocess."""
    if record is None:
        return
    used = int(usage.get('used_tokens', 0) or 0)
    model = str(usage.get('model', '') or '')
    if used <= 0 or not model:
        return
    # The FIRST reading of a chat is its floor: system prompt + project
    # instructions + injected docs, i.e. what a fresh chat would cost. Set
    # once per chat (start_new_chat clears it), never raised afterwards —
    # a later, larger reading is the growth we exist to report.
    #
    # …but ONLY if that first reading could actually BE a floor. A chat that
    # was already 490k tokens deep when this feature arrived would otherwise
    # adopt 490k as its baseline and report itself at 1.0x — a green light on
    # the single most expensive chat on the machine. Above the ceiling we
    # record nothing, the multiple stays unknown, and the indicator shows no
    # dot at all: "we cannot tell" is an honest answer, "safe" is not.
    baseline = int(getattr(record, 'context_baseline_tokens', 0) or 0)
    if baseline > 0:
        new_baseline = min(baseline, used)
    elif used <= _MAX_PLAUSIBLE_BASELINE_TOKENS:
        new_baseline = used
    else:
        # A chat already in progress. Its floor is not this reading — it is
        # the first turn of its own transcript, which the CLI has been
        # writing all along. Recovering it is what lets an EXISTING chat get
        # a cost reading instead of sitting blank until the operator starts a
        # new one. Still 0 when the transcript can't be found or read, and 0
        # means "show nothing", never "safe".
        new_baseline = _recovered_chat_floor(app, record)
    if (
        int(getattr(record, 'context_used_tokens', 0) or 0) == used
        and str(getattr(record, 'context_model', '') or '') == model
        and baseline == new_baseline
    ):
        return  # unchanged — don't rewrite the record on every poll
    try:
        record.context_used_tokens = used
        record.context_model = model
        record.context_baseline_tokens = new_baseline
        manager = app.config.get('SESSION_MANAGER')
        saver = getattr(manager, 'save_record', None) or getattr(
            manager, 'update_record', None,
        )
        if callable(saver):
            saver(record)
    except Exception:
        # Best-effort: a stale persisted reading is far better than losing
        # the live one, and this must never break the session payload.
        app.logger.exception('failed to persist context usage')


def _register_agent_version_route(app: Flask) -> None:
    @app.get('/api/agent-version')
    def get_agent_version():
        """Configured agent CLI version + capability flags (cached).

        Powers the "agent CLI out of date" banner and hides features the
        installed CLI can't run (e.g. the ultracode/workflow toggle). Probed
        once and cached; ``?refresh=1`` re-probes in place so the banner +
        upgrade button reflect a host-side CLI change (or a settings change)
        WITHOUT a kato restart — the UI's refresh control passes it.
        """
        # Per-backend: every task shows a tab per agent, so "is my CLI out of
        # date" is a question about the agent the operator is LOOKING at. A
        # single cached answer for the configured backend could never surface
        # a stale Codex CLI on a Claude-configured host.
        wanted = str(request.args.get('backend', '') or '').strip().lower()
        cache_key = f'AGENT_VERSION_INFO::{wanted}' if wanted else 'AGENT_VERSION_INFO'
        if _truthy_arg(request.args.get('refresh')):
            app.config.pop(cache_key, None)
            # A manual refresh must also re-ask the registry — otherwise a
            # release published during this process's lifetime stays invisible
            # until the published-version TTL lapses.
            try:
                from kato_core_lib.helpers.agent_version_utils import (
                    reset_latest_version_cache,
                )
                reset_latest_version_cache()
            except Exception:
                app.logger.exception('could not reset the published-version cache')
        cached = app.config.get(cache_key)
        if cached is None:
            try:
                from kato_core_lib.helpers.agent_version_utils import (
                    agent_version_info,
                )
                cached = agent_version_info(backend=wanted)
            except Exception:
                app.logger.exception('agent version probe failed')
                cached = {
                    'backend': 'unknown', 'binary': '', 'found': True,
                    'version': None, 'version_raw': '', 'recommended_min': '',
                    'up_to_date': True, 'latest_version': None,
                    'update_available': False, 'supports_workflows': False,
                    'detail': '',
                }
            app.config[cache_key] = cached
        return jsonify(cached)


def _register_agent_version_upgrade_route(app: Flask) -> None:
    @app.post('/api/agent-version/upgrade')
    def post_agent_version_upgrade():
        """START the gated, FIXED CLI upgrade in the background.

        Returns immediately with the first progress snapshot; the UI polls the
        GET below for the bar and the live log. Running it inline used to hold
        the request open for the whole install, so a reload or a proxy timeout
        lost a command that was still modifying the host.

        The operator's per-use approval happens in the UI (confirm); the
        server-side gate (opt-in setting + supported backend + non-docker) is
        enforced in ``upgrade_plan``. Failures are reported in the body (not an
        HTTP error) so the UI can show the message.
        """
        try:
            from kato_core_lib.helpers import agent_cli_upgrade_job
        except Exception:
            app.logger.exception('agent upgrade helper unavailable')
            return jsonify({'ok': False, 'state': 'error',
                            'message': 'upgrade helper unavailable'})
        # Upgrade the CLI the operator is LOOKING at, not the configured
        # one — the request names it, the same way the banner did.
        payload = request.get_json(silent=True) or {}
        wanted = str(payload.get('backend', '') or '').strip().lower()
        app.config.pop('AGENT_VERSION_INFO', None)
        app.config.pop(f'AGENT_VERSION_INFO::{wanted}', None)
        return jsonify(agent_cli_upgrade_job.start(backend=wanted))

    @app.get('/api/agent-version/upgrade')
    def get_agent_version_upgrade():
        """Progress snapshot for the in-flight (or last) upgrade.

        ``{state, percent, step, command, lines, ok, message, version_before,
        version_after}``. Poll-friendly and reload-safe: the job lives on the
        server, so closing the modal never orphans it.
        """
        try:
            from kato_core_lib.helpers import agent_cli_upgrade_job
        except Exception:
            app.logger.exception('agent upgrade helper unavailable')
            return jsonify({'ok': False, 'state': 'error',
                            'message': 'upgrade helper unavailable'})
        snapshot = agent_cli_upgrade_job.status()
        if snapshot.get('state') in ('done', 'error'):
            # The binary may have just changed under us — force the next
            # version GET to re-probe instead of serving the pre-upgrade cache.
            app.config.pop('AGENT_VERSION_INFO', None)
        return jsonify(snapshot)


# Action Guard categories whose remembered "allow always" must never
# be auto-resolved server-side without asking a human ONE more time —
# mirrors the (removed) client-side ``isHighRiskActionGuard`` carve-out
# in permissionEnvelope.js. A signature remembered as safe in one
# context (e.g. a bare ``mvn``) must not silently ride through when
# Action Guard now flags the SAME program name as touching credentials,
# exfiltrating data, remote-executing, or escaping the sandbox.
#: Label for the pseudo-repo holding the TASK FOLDER's own files. Not a
#: repository id — kept out of ``repository_ids`` so nothing tries to run
#: git, resolve a diff base, or open a pull request against it.
TASK_FOLDER_TREE_ID = 'task files'

_HIGH_RISK_ACTION_GUARD_CATEGORIES = frozenset({
    'credential_read', 'network_exfil', 'remote_exec', 'sandbox_escape',
})
# Tools whose approval can NEVER be remembered or auto-resolved, because
# approving them changes the agent's PERMISSIONS rather than performing a
# single action. ``ExitPlanMode`` is the whole of plan mode's enforcement:
# plan mode passes only ``--permission-mode plan`` with no tool denial, so
# the exit prompt is the gate. Remembering it disarms the lock globally and
# permanently. Kept as a set so the next such tool has an obvious home.
_NEVER_AUTO_RESOLVED_TOOLS = frozenset({'ExitPlanMode'})


def _maybe_auto_resolve_pending(
    app: Flask, session, task_id: str, request_id: str, outside_sandbox: bool,
) -> bool:
    """Resolve a still-pending request from a remembered backend
    decision BEFORE it is ever surfaced to the browser. Returns True
    when resolved — callers must skip showing/yielding this request.

    This is the server-side replacement for the (removed) client-side
    "recall and auto-submit" logic: the CHOICE of whether a pending ask
    needs a human is now made here, not in the browser — the client
    only ever sees requests the backend has already decided need one.
    Preserves the same safety carve-outs the client used to apply:
    never for an ``AskUserQuestion``-shaped ask, an out-of-sandbox
    write, or a high-risk Action Guard category.
    """
    if not request_id:
        return False
    from kato_core_lib.helpers.tool_decision_store import recall_tool_decision
    from kato_core_lib.helpers.tool_decision_utils import (
        decision_command_for,
        is_answerable_question,
    )
    tool_name, tool_input = _pending_tool(session, request_id)
    if not tool_name or is_answerable_question(tool_input):
        return False
    if outside_sandbox:
        return False
    # Leaving plan mode is the operator's decision, every time. A remembered
    # decision here is stored under the bare tool key (no command signature
    # for a non-Bash tool), so it is GLOBAL across tasks and survives
    # restarts: one "Allow always" would silently take every future plan
    # session — including the autonomous wait-planning hold — out of plan
    # mode with no human in the loop and no popup to notice. A safety lock
    # that can be permanently disarmed by one click on an unrelated task is
    # not a lock.
    if tool_name in _NEVER_AUTO_RESOLVED_TOOLS:
        return False
    verdict = _classify_action_for(session, tool_name, tool_input)
    if (
        verdict is not None
        and _action_guard_enum_value(verdict.category) in _HIGH_RISK_ACTION_GUARD_CATEGORIES
    ):
        return False
    remembered = recall_tool_decision(
        tool_name, decision_command_for(tool_name, tool_input),
    )
    if remembered is None:
        return False
    _resolve_permission_decision(
        app, session, task_id, request_id, remembered,
        'auto-resolved: remembered decision', hook_tool_name=tool_name,
    )
    return True


def _register_get_pending_permissions_route(app: Flask) -> None:
    @app.get('/api/permissions/pending')
    def get_pending_permissions():
        """Every unanswered permission ask across ALL live sessions that
        still needs a human — the backend auto-resolves anything a
        remembered decision already covers before it ever appears here.

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
            # A stopped/dead subprocess can't act on a permission, so never
            # surface its (now-stale) asks — otherwise the operator keeps
            # seeing approval popups for a session they already stopped.
            if getattr(session, 'is_alive', None) is False:
                continue
            probe = getattr(session, 'pending_control_requests', None)
            if not callable(probe):
                continue
            try:
                envelopes = probe() or []
            except Exception:
                # Best-effort by design (per docstring: one session's
                # failure must not fail the whole feed) — but silent,
                # unlike the auto-resolve-check failure a few lines
                # below, which already logs. If EVERY session's probe
                # starts failing (a real bug, not a one-off), this must
                # still be visible somewhere, or "no pending approvals"
                # becomes indistinguishable from "the poll is broken."
                app.logger.exception(
                    'pending_control_requests() failed for task %s',
                    record.task_id,
                )
                continue
            for envelope in envelopes:
                if not isinstance(envelope, dict):
                    continue
                envelope = dict(envelope)
                try:
                    resolved = _maybe_auto_resolve_pending(
                        app, session, record.task_id,
                        str(envelope.get('request_id') or ''),
                        bool(envelope.get('outside_sandbox')),
                    )
                except Exception:
                    resolved = False
                    app.logger.exception(
                        'auto-resolve check failed for a pending permission',
                    )
                if resolved:
                    continue
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
                # WHICH agent is asking. A task can hold a live chat with
                # each backend at once, so "wants permission" with no name
                # leaves the operator approving a command without knowing who
                # will run it — and the warning banner asserted "CLAUDE is
                # reaching outside the task folder" whoever asked.
                envelope['agent_backend'] = str(
                    getattr(record, 'agent_backend', '') or '',
                )
                pending.append(envelope)
        return jsonify({'pending': pending})


def _register_tool_decisions_routes(app: Flask) -> None:
    """Manage backend-owned remembered permission decisions.

    Drives the Settings → Permissions panel, which used to read/write
    the browser's ``localStorage`` directly — the backend is now the
    only place a remembered decision lives (see ``tool_decision_store``
    module docstring), so the panel reads/mutates it through here.
    """
    @app.get('/api/tool-decisions')
    def get_tool_decisions():
        from kato_core_lib.helpers.tool_decision_store import list_tool_decisions
        return jsonify({'decisions': list_tool_decisions()})

    @app.post('/api/tool-decisions/set')
    def set_tool_decision_route():
        """Change the scope of an existing (or brand new) remembered
        decision — the Settings panel's allow/deny dropdown, which acts
        without a live pending permission to attach to."""
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        payload = request.get_json(silent=True) or {}
        tool_name = text_from_mapping(payload, 'tool_name')
        if not tool_name:
            return jsonify({'error': 'tool_name is required'}), 400
        command_signature = str(payload.get('command_signature', '') or '')
        remember_tool_decision(tool_name, command_signature, bool(payload.get('allow', False)))
        return jsonify({'saved': True})

    @app.post('/api/tool-decisions/forget')
    def forget_tool_decision_route():
        from kato_core_lib.helpers.tool_decision_store import forget_tool_decision
        payload = request.get_json(silent=True) or {}
        tool_name = text_from_mapping(payload, 'tool_name')
        if not tool_name:
            return jsonify({'error': 'tool_name is required'}), 400
        command_signature = str(payload.get('command_signature', '') or '')
        forget_tool_decision(tool_name, command_signature)
        return jsonify({'forgotten': True})

    @app.post('/api/tool-decisions/clear')
    def clear_tool_decisions_route():
        from kato_core_lib.helpers.tool_decision_store import clear_all_tool_decisions
        clear_all_tool_decisions()
        return jsonify({'cleared': True})


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
        from agent_core_lib.agent_core_lib.helpers.sandbox_scope import (
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


def _publish_action_guard_block(session, verdict, operator_approved=False) -> None:
    """Surface a hard BLOCK as a loud system bubble in the session feed.

    ``operator_approved`` distinguishes the two ways a hard block reaches the
    feed so the bubble states what actually happened. When the operator
    clicked *Approve* on a floor category the message must NOT read "the agent
    was refused" as if they weren't involved — they approved; the block is a
    hard safety floor that overrides the approval. Either way it stays a loud
    (red) notice — only the wording changes.
    """
    publisher = getattr(session, 'publish_system_notice', None)
    if not callable(publisher):
        return
    category = _action_guard_enum_value(verdict.category)
    reason = verdict.reason or 'blocked by Action Guard'
    if operator_approved:
        message = (
            f'BLOCKED by Action Guard ({category}): {reason}. You approved this, '
            f'but it is a hard safety floor that can’t be overridden — the '
            f'action was refused and the agent was told why.'
        )
    else:
        message = (
            f'BLOCKED by Action Guard ({category}): {reason}. You denied this — '
            f'the action was refused and the agent was told why.'
        )
    try:
        publisher(
            CLAUDE_SYSTEM_SUBTYPE_ACTION_GUARD_BLOCK,
            message,
            {'action_guard': {
                'category': category,
                'decision': 'block',
                'reason': reason,
                'rule_id': verdict.rule_id,
                'operator_approved': bool(operator_approved),
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
    requested = str(_get_task_override(app, 'TASK_EFFORT_OVERRIDES', task_id) or '')
    if not requested:
        return False  # Auto / no override — never force a respawn
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not getattr(session, 'is_alive', False):
        return False  # no live session — the spawn path applies the effort
    if bool(getattr(session, 'is_working', False)):
        return False  # don't interrupt a turn
    return str(getattr(session, 'effort', '') or '') != requested


def _tool_floor_change_needs_respawn(app: Flask, manager, task_id: str, images) -> bool:
    """Respawn when kato's non-overridable tool floor changed under a session.

    ``--disallowedTools`` is baked into the subprocess at spawn time, so a
    session that started before a kato upgrade keeps the OLD floor for its
    whole life. When the floor is LOOSENED — the git denylist being narrowed
    to the commands the orchestrator actually owns, say — every task already
    in progress would go on refusing commands kato now permits, and the
    operator would have no way to tell why except restarting each one by
    hand.

    Compares what the live subprocess was spawned with against what a fresh
    spawn would use, and respawns on the next message if they differ.
    Deliberately symmetric: a TIGHTENED floor must reach a running session
    too, or an upgrade that closes a hole would leave every in-flight
    session still holding it open.

    Mid-turn is left alone (like the model/effort checks): interrupting a
    working agent to change a tool list it has not hit yet loses the turn
    for nothing. It applies on the next message instead.
    """
    session = manager.get_session(task_id) if manager else None
    if session is None or not getattr(session, 'is_alive', False):
        return False
    if getattr(session, 'is_working', False) or images:
        return False
    live = str(getattr(session, 'disallowed_tools', '') or '')
    if not live:
        # Nothing recorded (an older session object) — a respawn on a guess
        # would drop the operator's conversation for no proven reason.
        return False
    try:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        expected = ClaudeCliClient._merge_disallowed_with_floor(
            _configured_disallowed_tools(app),
            bypass_permissions=_session_is_bypass(session),
        )
    except Exception:
        return False
    return _tool_csv_set(live) != _tool_csv_set(expected)


def _tool_csv_set(value: str) -> frozenset:
    """Order-insensitive comparison — the floor is a SET, not a sequence."""
    return frozenset(
        entry.strip() for entry in str(value or '').split(',') if entry.strip()
    )


def _session_is_bypass(session) -> bool:
    return str(getattr(session, 'permission_mode', '') or '') == 'bypassPermissions'


def _configured_disallowed_tools(app: Flask) -> str:
    """The operator's own disallowed-tools setting (empty when unset)."""
    config = app.config.get('KATO_CONFIG') or {}
    try:
        return str(getattr(getattr(config, 'claude', None), 'disallowed_tools', '') or '')
    except Exception:
        return ''


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
    requested = str(_get_task_override(app, 'TASK_MODEL_OVERRIDES', task_id) or '')
    if not requested:
        return False  # no override — never force a respawn
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not getattr(session, 'is_alive', False):
        return False  # no live session — the spawn path applies the model
    if bool(getattr(session, 'is_working', False)):
        return False  # don't interrupt a turn
    return str(getattr(session, 'model', '') or '') != requested


def _plan_mode_change_needs_respawn(app: Flask, manager, task_id: str, images) -> bool:
    """True when a live, idle session must respawn to apply the plan lock.

    Same shape as ``_effort_change_needs_respawn``: the Claude CLI bakes
    ``--permission-mode`` at spawn time, so flipping the composer's plan
    lock can't change a running subprocess. Without this, a session that
    was spawned able to edit keeps editing even after the operator locks
    it to planning-only — the opposite of "lock him so he never
    implements". Fires for any difference between the requested mode and
    the live session's, in either direction (lock on AND unlock), so
    turning the lock back off also respawns out of plan mode.

    Tightening (selecting Explain or Plan) fires immediately and
    interrupts a running turn — the operator is asking the agent to stop
    changing code, and deferring that is indistinguishable from ignoring
    it. Loosening waits for an idle session and lets images through,
    because letting a read-only turn finish costs nothing.
    """
    overrides = app.config.get('TASK_PLAN_MODE_OVERRIDES')
    if overrides is None:
        return False
    session = manager.get_session(task_id) if manager is not None else None
    if session is None or not getattr(session, 'is_alive', False):
        return False  # no live session — the spawn path applies the mode
    requested = str(overrides.get(task_id, '') or '')
    # Compare RESTRICTION, not the raw mode string. There are two independent
    # ways a session can be locked down and they are baked at spawn time:
    # ``--permission-mode plan``, and Explain's read-only tool denial. A
    # comparison that looked only at ``permission_mode`` would see Explain's
    # resolved 'default' and call the session unchanged — forwarding the
    # message into a subprocess that can still edit.
    if _requested_restriction(requested) == _live_restriction(session):
        return False
    # TIGHTENING vs LOOSENING are not symmetric, and treating them as if
    # they were is what let a session keep editing under Explain.
    #
    # Selecting Explain (or Plan) is the operator saying "stop changing my
    # code". The old order deferred on ``is_working`` and on attached
    # images — both of which fall through to ``_deliver_to_live_session``,
    # i.e. straight into the subprocess that still holds every mutating
    # tool. Switching to Explain mid-turn therefore did nothing at all:
    # the CLI bakes the tool denial at spawn, so only a respawn can apply
    # it, and the one path that respawns had just declined to.
    #
    # So: a restriction takes effect NOW, interrupting the turn if one is
    # running. Removing a restriction still waits for idle and still lets
    # images through — continuing a read-only turn harms nothing.
    if _requested_restriction(requested):
        if images:
            app.logger.warning(
                'task %s: applying a mode restriction requires a respawn, '
                'which cannot carry attached images — the text is sent, the '
                'images are not', task_id,
            )
        return True
    if images:
        return False
    return not bool(getattr(session, 'is_working', False))


def _stop_live_session_on_tightening(app: Flask, task_id: str, requested: str) -> bool:
    """Kill the live subprocess when the operator TIGHTENS the mode.

    Selecting Plan or Explain is a safety decision, and it used to do
    nothing until the operator happened to send another message: the
    override was written, the running subprocess kept its spawn-time
    ``--permission-mode``, and the agent carried on editing files. That is
    the "I kept it on Plan but it is still editing" report — the lock was
    real, it just applied later than anyone would assume.

    Only TIGHTENING stops a session. Loosening (Plan → Edit automatically)
    deliberately does not: interrupting a working agent to give it MORE
    permission has no safety value and would throw away an in-flight turn.
    The next message respawns via ``--resume``, so no context is lost.

    Returns whether a session was actually stopped, for the response body.
    """
    if not _requested_restriction(requested):
        return False
    manager = app.config.get('SESSION_MANAGER')
    if manager is None:
        return False
    try:
        session = manager.get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if _live_restriction(session) == _requested_restriction(requested):
            return False
        # Keep the record: the mode change must not lose the chat history
        # or the resume id — the next message picks the session back up.
        manager.terminate_session(task_id, remove_record=False)
    except Exception:
        # A failed stop must not fail the operator's choice; the override is
        # already persisted and the next spawn honours it either way.
        return False
    return True


def _requested_restriction(requested: str) -> str:
    """The restriction the operator's current selection asks for."""
    if requested == PLAN_PERMISSION_MODE:
        return PLAN_PERMISSION_MODE
    if is_explain_mode(requested):
        return EXPLAIN_MODE
    return ''


def _live_restriction(session) -> str:
    """The restriction actually baked into the running subprocess."""
    if str(getattr(session, 'permission_mode', '') or '') == PLAN_PERMISSION_MODE:
        return PLAN_PERMISSION_MODE
    if session_is_in_explain_mode(session):
        return EXPLAIN_MODE
    return ''


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
    cwd, summary, description = _chat_resume_context(
        manager, workspace_manager, task_id,
    )
    additional_dirs = _chat_additional_dirs(workspace_manager, task_id)
    model_override = _get_task_override(app, 'TASK_MODEL_OVERRIDES', task_id)
    # No per-task override (or it was cleared) → pass the concrete chat default
    # explicitly, so kato never falls through to the CLI's opaque built-in
    # effort (the old "Auto"). The operator always knows the level that ran.
    effort_override = (
        _get_task_override(app, 'TASK_EFFORT_OVERRIDES', task_id)
        or _configured_chat_effort(app)
    )
    # Plan-mode lock: when set, force ``--permission-mode plan`` so the
    # spawned agent can only plan. Empty → '' so the runner falls back to
    # its configured default mode (the normal can-implement chat session).
    plan_overrides = app.config.get('TASK_PLAN_MODE_OVERRIDES') or {}
    permission_mode = plan_overrides.get(task_id, '')
    try:
        runner.resume_session_for_chat(
            task_id=task_id,
            message=text,
            cwd=cwd,
            task_summary=summary,
            task_description=description,
            # The TASK FOLDER — the boundary the operator means by "never
            # leave the task". It scopes the prompt's STRICT BOUNDARY block
            # and, in docker mode, is what the container bind-mounts (cwd is
            # one repo clone, so mounting that hides the task's other repos).
            workspace_root=_task_workspace_root(workspace_manager, task_id),
            additional_dirs=additional_dirs,
            model=model_override,
            effort=effort_override,
            permission_mode=permission_mode,
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
    list_comments = getattr(
        getattr(agent_service, 'comments', agent_service), 'list_task_comments', None)
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
    target_cwd, _summary, _description = _chat_resume_context(
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


def _chat_resume_context(
    session_manager, workspace_manager, task_id: str,
) -> tuple[str, str, str]:
    """Best-effort lookup of cwd + summary + description for a chat-respawn.

    Falls back across managers because either side might be missing
    (kato/sessions wiped, or workspace metadata not yet populated).

    The description comes from the workspace record only — the session record
    never carried one. It is cached at provision time precisely so this lookup
    stays offline; re-querying the tracker on every chat spawn would put the
    ticket API on the interactive path.
    """
    cwd = ''
    summary = ''
    description = ''
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
            description = str(getattr(workspace, 'task_description', '') or '')
            if not cwd and getattr(workspace, 'repository_ids', None):
                first_repo = workspace.repository_ids[0]
                try:
                    cwd = str(workspace_manager.repository_path(task_id, first_repo))
                except Exception:
                    cwd = ''
    return cwd, summary, description


def _task_workspace_root(workspace_manager, task_id: str) -> str:
    """The task's own folder — parent of every repo clone for this task.

    Empty when the workspace manager can't produce one or the directory
    doesn't exist. Never derived from a repo path by walking upward: an
    adopted checkout's parent could be the operator's entire source root, and
    handing THAT out as a scope boundary (or bind-mounting it) would be
    strictly worse than the per-repo scope it replaced.
    """
    if workspace_manager is None:
        return ''
    try:
        path = workspace_manager.workspace_path(task_id)
    except Exception:
        return ''
    text = str(path or '')
    return text if text and Path(text).is_dir() else ''


def _chat_additional_dirs(workspace_manager, task_id: str) -> list[str]:
    """Task-workspace-folder ``--add-dir`` path for the chat spawn.

    Thin alias over the shared ``sibling_repository_dirs`` helper so the
    chat-send route and the comment-run respawn surface the SAME scope
    (a multi-repo task's agent must reach every repo, not just ``cwd`` —
    including a repo attached to the task after this session spawned).
    """
    from kato_core_lib.helpers.workspace_repo_utils import (
        sibling_repository_dirs,
    )
    return sibling_repository_dirs(workspace_manager, task_id)


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
    manager, workspace_manager, task_id: str, agent_service=None, app=None,
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
        yield from _replay_history(record, agent_session_id)
        if _drain_queued_task_comment(agent_service, task_id):
            session = manager.get_session(task_id) if manager is not None else None
            if session is not None:
                replayed_count = yield from _replay_session_backlog(
                    session, agent_service=agent_service, task_id=task_id, app=app,
                )
                yield from _follow_live_session(
                    session, start_index=replayed_count,
                    agent_service=agent_service, task_id=task_id, app=app,
                )
                return
        idle_payload = _record_to_dict(record) if record is not None else {}
        yield _sse_message(SSE_EVENT_SESSION_IDLE, idle_payload)
        return
    yield from _replay_preflight_log(workspace_manager, task_id)
    yield from _replay_history(record, agent_session_id)
    replayed_count = yield from _replay_session_backlog(
        session, agent_service=agent_service, task_id=task_id, app=app,
    )
    yield from _follow_live_session(
        session, start_index=replayed_count,
        agent_service=agent_service, task_id=task_id, app=app,
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
from claude_core_lib.claude_core_lib.helpers.context_window import (
    context_window_tokens,
    widen_window_to_observed,
)


def _replay_history(record, agent_session_id: str):
    """Replay a chat's transcript, from whichever CLI wrote it.

    Each backend persists its own way and neither can read the other's, so
    the record's backend picks the reader. Codex used to have NO reader at
    all: its events lived only in the session object's memory, so a chat
    survived a page reload but not a restart of the process hosting it — the
    operator came back to an empty tab.
    """
    from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
    backend = str(getattr(record, 'agent_backend', '') or '').strip().lower()
    if backend == AgentBackend.CODEX.value:
        yield from _replay_codex_history_from_disk(record)
        return
    # Claude, and legacy records with no backend recorded (all Claude).
    yield from _replay_history_from_disk(agent_session_id)


def _replay_codex_history_from_disk(record):
    """Codex's rollout transcript, in the live stream's own wire shape.

    Read from the record's OWN id rather than the resolver the other
    transport uses: that one deliberately answers '' for a Codex chat, so it
    can never hand Claude's transcript to a Codex tab.
    """
    session_id = str(getattr(record, 'agent_session_id', '') or '').strip()
    if not session_id:
        return
    try:
        from codex_core_lib.codex_core_lib.session.history import (
            load_history_events as load_codex_history,
        )
    except ImportError:
        return
    try:
        events = load_codex_history(session_id)
    except Exception:
        return
    for raw in events:
        yield _sse_message(
            SSE_EVENT_SESSION_HISTORY_EVENT,
            {'event': {'received_at_epoch': 0, 'raw': raw}},
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


def _maybe_auto_resolve_live_event(app, session, task_id: str, event) -> bool:
    """Same server-side auto-resolve check as
    ``_maybe_auto_resolve_pending``, applied to a LIVE ``control_request``
    event about to be yielded over SSE. Returns True when resolved —
    callers must not yield this event to the browser.

    ``app`` is None for the direct-generator call sites used in tests
    (no Flask app in scope there) — auto-resolve is simply skipped, same
    as before this feature existed.
    """
    if app is None:
        return False
    payload = event.to_dict()
    raw = payload.get('raw') if isinstance(payload, dict) else None
    if not isinstance(raw, dict) or raw.get('type') != CLAUDE_EVENT_CONTROL_REQUEST:
        return False
    request_id = str(raw.get('request_id') or '')
    try:
        return _maybe_auto_resolve_pending(
            app, session, task_id, request_id, bool(raw.get('outside_sandbox')),
        )
    except Exception:
        app.logger.exception('auto-resolve check failed for a live permission event')
        return False


def _pending_control_request_ids(session):
    """The request_ids of control asks STILL awaiting an answer for this
    session (a resolved/answered ask is ``pop``'d from the session's pending
    map, so it won't appear here), or ``None`` when the session can't report.

    ``None`` is the fail-OPEN signal — the caller must then NOT suppress any
    ask, because dropping a genuinely-pending permission (the operator never
    sees it) is far worse than replaying an already-answered one."""
    probe = getattr(session, 'pending_control_requests', None)
    if not callable(probe):
        return None
    try:
        pending = probe() or []
    except Exception:
        return None
    return {
        str((item or {}).get('request_id') or '')
        for item in pending
        if isinstance(item, dict)
    }


def _control_request_already_answered(raw, pending_ids) -> bool:
    """True when ``raw`` is a ``control_request`` whose ask has ALREADY been
    answered — i.e. we have a definitive pending set and its request_id is not
    in it. Replaying such an event on reconnect (the operator switching back to
    the task's tab) re-pops the permission modal for a decision they already
    made, even on an idle session. ``pending_ids is None`` (can't tell) or a
    non-control_request event → False (replay it, never suppress)."""
    if pending_ids is None:
        return False
    if not isinstance(raw, dict) or raw.get('type') != CLAUDE_EVENT_CONTROL_REQUEST:
        return False
    request_id = str(raw.get('request_id') or '')
    if not request_id:
        return False
    return request_id not in pending_ids


def _replay_session_backlog(session, agent_service=None, task_id='', app=None):
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

    Already-ANSWERED ``control_request`` events are dropped from the replay:
    the backlog keeps the original ask, so without this switching back to a
    task's tab re-pops the permission modal for a decision the operator
    already made (even when the session is idle) — the "it asks for all the
    permissions again every time I switch to the task" report. A still-pending
    ask IS replayed so a genuinely-unanswered permission still surfaces.
    """
    backlog = session.recent_events()
    pending_ids = _pending_control_request_ids(session)
    for event in backlog:
        payload = event.to_dict()
        raw = payload.get('raw') if isinstance(payload, dict) else None
        if _control_request_already_answered(raw, pending_ids):
            continue
        if _maybe_auto_resolve_live_event(app, session, task_id, event):
            continue
        yield _session_event_frame(event, session)
    return len(backlog)


def _follow_live_session(
    session, start_index: int = 0, agent_service=None, task_id: str = '', app=None,
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
            if _maybe_auto_resolve_live_event(app, session, task_id, event):
                continue
            yield _session_event_frame(event, session)
            _advance_task_comments_after_result(event, agent_service, task_id)

        if not session.is_alive:
            # Drain any final events that landed between the slice
            # and ``is_alive`` flipping, then close.
            tail, last_index = session.events_after(last_index)
            for event in tail:
                if _maybe_auto_resolve_live_event(app, session, task_id, event):
                    continue
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
        getattr(agent_service, 'comment_runs', agent_service), 'complete_in_progress_task_comments', None,
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
    drain = getattr(
        getattr(agent_service, 'comment_runs', agent_service), 'drain_next_queued_task_comment', None)
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
    session_manager, workspace_manager, agent_service=None, app=None,
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
    pending_permission_tool_by_task = _pending_permission_tool_by_task(session_manager, app=app)
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
    backend_by_task = _backend_by_task(session_manager, workspace_records)
    awaiting_push = getattr(getattr(agent_service, 'publish', agent_service), 'is_awaiting_push_approval', None)
    return [
        _workspace_record_to_dict(
            record,
            live_session_ids,
            session_ids_by_task,
            awaiting_push,
            working_session_ids=working_session_ids,
            pending_permission_session_ids=pending_permission_session_ids,
            pending_permission_tool_by_task=pending_permission_tool_by_task,
            backend_by_task=backend_by_task,
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

    ``list_records`` failing is logged, not just swallowed: this walk
    backs ``/api/permissions/pending`` — the operator's ONLY visibility
    into a pending tool-approval request for a backgrounded task. A
    silent failure here returns an empty list indistinguishable from
    "nothing needs approval," which can leave an agent sitting blocked
    on an approval the operator is never shown, with zero signal
    anything went wrong.
    """
    if session_manager is None:
        return
    try:
        records = session_manager.list_records()
    except Exception:
        logging.getLogger(__name__).exception(
            'session_manager.list_records() failed — pending-permission '
            'poll and live-session visibility will see NO sessions this '
            'cycle, not just none needing approval',
        )
        return
    for record in records:
        try:
            session = session_manager.get_session(record.task_id)
        except Exception:
            logging.getLogger(__name__).exception(
                'get_session(%s) failed while walking live sessions',
                record.task_id,
            )
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
        logging.getLogger(__name__).exception(
            'session_manager.list_records() failed in _session_ids_by_task',
        )
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


def _pending_permission_tool_by_task(session_manager, app=None) -> dict[str, str]:
    """Per-task ``{task_id: pending_tool_name}`` for the tab-attention path.

    Returns the tool name on the most recent unanswered permission
    request so the UI can decide whether to mark the tab orange.
    Auto-resolves against a remembered decision first (same check the
    ``/api/permissions/pending`` poll and the SSE stream already run) —
    a request that gets silently allowed/denied must not flash the tab
    orange for the split second before the other paths catch up.
    ``app`` is None for the direct-call test sites; auto-resolve is
    then simply skipped (a request just stays "pending" until the
    other paths resolve it, same as before this feature existed).
    """
    pending: dict[str, str] = {}
    for record, session in _iter_live_sessions(session_manager):
        tool_name = _session_pending_permission_tool(session)
        if not tool_name:
            continue
        if app is not None and _auto_resolve_newest_pending(app, session, record.task_id):
            continue
        # Empty-string tool name still marks pending (legacy
        # callers + back-compat) — the UI's filter just can't
        # match it to a remembered decision.
        pending[record.task_id] = tool_name
    return pending


def _auto_resolve_newest_pending(app, session, task_id: str) -> bool:
    """Best-effort: auto-resolve a session's most recent still-pending
    control request from a remembered decision, for the tab-attention
    path. Mirrors ``_maybe_auto_resolve_pending`` (used by the
    ``/api/permissions/pending`` poll and the SSE stream) so all THREE
    surfacing paths agree — the client never needs to reason about
    remembered decisions itself, even for the tab-attention indicator.
    """
    probe = getattr(session, 'pending_control_requests', None)
    if not callable(probe):
        return False
    try:
        envelopes = probe() or []
    except Exception:
        return False
    if not envelopes or not isinstance(envelopes[-1], dict):
        return False
    envelope = envelopes[-1]
    try:
        return _maybe_auto_resolve_pending(
            app, session, task_id,
            str(envelope.get('request_id') or ''),
            bool(envelope.get('outside_sandbox')),
        )
    except Exception:
        app.logger.exception(
            'auto-resolve check failed for tab-attention pending permission',
        )
        return False


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


def _backend_by_task(session_manager, records) -> dict[str, str]:
    """Which agent each task's chat runs on, resolved for the whole list.

    ``backend_for`` already answers "the record's backend, else the
    configured default", which is exactly what the operator needs to see: a
    task whose chat has never been started still runs on SOMETHING, and
    naming that is more useful than naming nothing.

    Never raises — a status chip must not be able to fail the session list.
    """
    resolver = getattr(session_manager, 'backend_for', None)
    if not callable(resolver):
        # Single-backend host: one manager, and it names itself.
        fallback = str(getattr(session_manager, 'AGENT_BACKEND', '') or '')
        return {
            str(getattr(r, 'task_id', '') or ''): fallback for r in records
        } if fallback else {}
    found = {}
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        try:
            found[task_id] = str(resolver(task_id) or '').strip().lower()
        except Exception:
            found[task_id] = ''
    return found


def _workspace_record_to_dict(
    record,
    live_session_ids: set[str],
    session_ids_by_task: dict[str, str] | None = None,
    awaiting_push_check=None,
    *,
    working_session_ids: set[str] | None = None,
    pending_permission_session_ids: set[str] | None = None,
    pending_permission_tool_by_task: dict[str, str] | None = None,
    backend_by_task: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = record.to_dict() if hasattr(record, 'to_dict') else dict(record)
    # WORKSPACE records carry no backend — they predate agent tabs and are
    # about the clone on disk, not the chat. Without this the UI had nothing
    # to name the agent with and fell back to the literal word "Agent" on the
    # status chip, which tells the operator nothing: the whole question is
    # WHICH agent. The session layer knows, so it answers here.
    payload['agent_backend'] = (backend_by_task or {}).get(record.task_id, '')
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

    # Same resolver the orchestrator uses. These two must not drift: the
    # webserver can run without a live orchestrator, and if they disagree the
    # UI reads session metadata from a directory the agent never writes to.
    return ClaudeSessionManager(state_dir=kato_session_state_dir(fallback_state_dir))


def main() -> None:
    """Run the dev server. Use kato.main for a real run with shared state."""
    app = create_app()
    host = os.environ.get('KATO_WEBSERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('KATO_WEBSERVER_PORT', '5050'))
    ssl_context = None
    if str(os.environ.get('KATO_WEBSERVER_HTTPS', '1')).strip().lower() not in {
        '0', 'false', 'no', 'off',
    }:
        from kato_core_lib.helpers.tls_cert_utils import ensure_local_tls_cert
        ssl_context = ensure_local_tls_cert(logger=app.logger, install_trust=True)
    scheme = 'https' if ssl_context else 'http'
    app.logger.info('dev server listening on %s://%s:%s', scheme, host, port)
    app.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_context)


if __name__ == '__main__':  # pragma: no cover - module-as-script guard, never hit under import-based tests
    main()
