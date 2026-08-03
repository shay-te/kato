"""Git working-tree status parsing and artifact detection utilities."""
from __future__ import annotations

from utils_core_lib.utils_core_lib.text_utils import normalized_text


GENERATED_ARTIFACT_ROOTS = {'build', 'dist', 'out', 'coverage', 'target'}

# Cache / bytecode dirs that appear ANYWHERE in the tree (not just at the
# repo root). Committing these is never intended, and the Python bytecode
# caches are actively dangerous: a compiled credential-pattern test
# (``…/__pycache__/test_credential_patterns.cpython-311.pyc``) bakes
# secret-looking strings into history and trips GitHub Push Protection
# (GH013), which rejects the WHOLE branch push and can't be undone without
# a history rewrite — the pii-core-lib "stuck 2 commits ahead" incident.
# The root-only ``GENERATED_ARTIFACT_ROOTS`` check missed these because
# they sit nested (``pii_core_lib/tests/__pycache__/…``).
GENERATED_ARTIFACT_DIR_SEGMENTS = {
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
}
GENERATED_ARTIFACT_SUFFIXES = ('.pyc', '.pyo')


def _artifact_exclusion_path(path: str) -> str:
    """The path to reset+clean if ``path`` is a generated artifact, else ''.

    A recognized root (build/dist/…) collapses to that root dir; a nested
    cache dir collapses to the cache dir itself (so the whole dir is
    removed in one ``git clean``); a loose ``.pyc``/``.pyo`` is returned
    as-is. ``validation_report.md`` is handled separately and never
    treated as a generic artifact here.
    """
    if path.endswith('validation_report.md'):
        return ''
    segments = path.split('/')
    if segments[0] in GENERATED_ARTIFACT_ROOTS:
        return segments[0]
    for index, segment in enumerate(segments):
        if segment in GENERATED_ARTIFACT_DIR_SEGMENTS:
            return '/'.join(segments[: index + 1])
    if path.endswith(GENERATED_ARTIFACT_SUFFIXES):
        return path
    return ''


def status_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if ' -> ' in path:
            path = path.split(' -> ', 1)[1]
        normalized_path = normalized_text(path).rstrip('/')
        if normalized_path:
            paths.append(normalized_path)
    return paths


def validation_report_paths_from_status(status_output: str) -> list[str]:
    return [
        path
        for path in status_paths(status_output)
        if path.endswith('validation_report.md')
    ]


def generated_artifact_paths_from_status(status_output: str) -> list[str]:
    generated_artifact_paths: list[str] = []
    for path in status_paths(status_output):
        exclusion = _artifact_exclusion_path(path)
        if exclusion and exclusion not in generated_artifact_paths:
            generated_artifact_paths.append(exclusion)
    return generated_artifact_paths


def status_contains_only_removable_artifacts(
    status_output: str,
    generated_artifact_paths: list[str],
    validation_report_paths: list[str],
) -> bool:
    # ``generated_artifact_paths`` is accepted for back-compat; removability
    # is decided by the shared predicate so nested caches / bytecode count
    # the same way they're excluded at commit time.
    removable_reports = set(validation_report_paths)
    for path in status_paths(status_output):
        if path in removable_reports:
            continue
        if _artifact_exclusion_path(path):
            continue
        return False
    return True


def git_ready_command_summary(
    destination_branch: str,
    *,
    include_remote_sync: bool,
) -> str:
    commands = [f'git checkout -f {destination_branch}']
    if include_remote_sync:
        commands.insert(0, 'git fetch origin')
        commands.append(f'git reset --hard origin/{destination_branch}')
    commands.append('git clean -fd')
    return ' && '.join(commands)
