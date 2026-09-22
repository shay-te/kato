"""Every provider's connection check must surface the server's explanation.

An operator setting kato up for the first time hit:

    startup dependency validation failed: - unable to validate youtrack:
    400 Client Error: Bad Request for url: .../api/issues?query=project%3A+UNA+...

The server had answered with a body naming the cause. ``raise_for_status()``
discards the body, so the operator got a percent-encoded query to decode by
hand and no statement of what was wrong with it.

``RetryingClientBase.raise_for_status_with_detail`` already existed and already
knew every provider's error shape — Bitbucket's nested
``{"error": {"message", "fields"}}``, GitHub/GitLab's flat ``message``,
YouTrack's ``error_description``. It was simply not being called. Six of the
seven ``validate_connection`` methods had the same defect as YouTrack's.

This is a RATCHET: it fails when a provider regresses, and it covers a new
provider on the day it is added.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Libraries that talk HTTP to a task/VCS provider.
PROVIDER_LIBS = (
    'bitbucket_core_lib',
    'github_core_lib',
    'gitlab_core_lib',
    'jira_core_lib',
    'youtrack_core_lib',
    'provider_client_base',
)

# The methods an operator reaches during setup / the scan loop, where a bare
# status code is useless and the body is the whole answer.
OPERATOR_FACING = ('validate_connection', 'get_assigned_tasks')

DETAILED = 'raise_for_status_with_detail'


def _client_modules() -> list[Path]:
    found: list[Path] = []
    for lib in PROVIDER_LIBS:
        root = REPO_ROOT / lib
        if not root.is_dir():
            continue
        for path in root.rglob('*.py'):
            parts = path.parts
            if '__pycache__' in parts or 'tests' in parts:
                continue
            found.append(path)
    return found


def _bare_raise_calls(node: ast.AST) -> bool:
    """Does this subtree call ``<something>.raise_for_status()`` directly?"""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == 'raise_for_status':
            return True
    return False


class ProviderErrorDetailGate(unittest.TestCase):

    def test_the_shared_helper_exists_where_every_client_inherits_it(self) -> None:
        # If this moves, the gate below is asserting against a ghost.
        source = (
            REPO_ROOT / 'provider_client_base' / 'provider_client_base'
            / 'retrying_client_base.py'
        ).read_text()
        self.assertIn(f'def {DETAILED}', source)

    def test_no_operator_facing_method_uses_the_bare_call(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in _client_modules():
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in OPERATOR_FACING:
                    continue
                checked += 1
                if _bare_raise_calls(node):
                    offenders.append(
                        f'{path.relative_to(REPO_ROOT)}:{node.lineno} '
                        f'{node.name}() uses response.raise_for_status(); '
                        f'call self.{DETAILED}(response) instead'
                    )

        # A scan that found nothing to check is a broken scan, not a pass.
        self.assertGreater(
            checked, 5,
            'found almost no validate_connection/get_assigned_tasks methods — '
            'did the client layout move?',
        )
        self.assertEqual(
            offenders, [],
            'provider connection checks that swallow the server explanation:\n  '
            + '\n  '.join(offenders),
        )

    def test_the_gate_would_catch_a_planted_regression(self) -> None:
        # Guard against the walk silently matching nothing: a method shaped
        # like the old code must be reported.
        planted = ast.parse(
            'def validate_connection(self):\n'
            '    response = self._get_with_retry("/x")\n'
            '    response.raise_for_status()\n'
        )
        method = planted.body[0]
        self.assertTrue(_bare_raise_calls(method))

    def test_the_gate_accepts_the_detailed_form(self) -> None:
        accepted = ast.parse(
            'def validate_connection(self):\n'
            '    response = self._get_with_retry("/x")\n'
            f'    self.{DETAILED}(response)\n'
        )
        self.assertFalse(_bare_raise_calls(accepted.body[0]))


if __name__ == '__main__':
    unittest.main()
