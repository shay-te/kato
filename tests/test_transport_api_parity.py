"""The two CLI transports must present the SAME interface.

Kato picks a backend from config and then calls it. Every place that has to
ask "which backend is this?" before choosing a name or an argument is a place
the two can silently diverge — and they did: for months one transport's review
prompt read a comment's line from a field the other didn't, and the bug was
fixed on one side only.

So this file locks the shape, not the behaviour:

* the client classes expose the same public methods, with the same signatures,
  and the same constructor keyword arguments and defaults;
* the helper modules every transport ships have the same paths, the same
  public names, and the same signatures.

Where they legitimately differ (the default binary name, a CLI-specific
private helper) the difference is named here explicitly, so adding a new one
is a deliberate edit rather than a silent drift.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from codex_core_lib.codex_core_lib.cli_client import CodexCliClient

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Helper modules every CLI transport ships, at the same path, with the same
# public API. A transport that adds one here must add it everywhere.
_SHARED_HELPER_MODULES = (
    'helpers/model_catalog.py',
    'helpers/effort_levels.py',
    'helpers/one_shot_utils.py',
)

# Private methods a transport may own alone, because the CLIs differ there.
# Each entry is a real difference, not an exemption of convenience.
_TRANSPORT_ONLY_METHODS = {
    # Claude: a tool-permission model and a single JSON payload.
    '_permission_mode', '_parse_json_payload', '_extract_first_json_object',
    '_merge_allowed_with_read_only_allowlist', '_merge_disallowed_with_floor',
    '_merge_disallowed_with_git_deny', '_union_disallowed',
    # Codex: no system-prompt channel, and a JSONL event stream.
    '_system_prompt_addendum', '_parse_jsonl_payload',
}

# Public signatures that differ only by the transport's own name.
_BINARY_DEFAULTS = {'claude', 'codex'}


def _public_api(path: Path) -> dict[str, str]:
    api: dict[str, str] = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
            api[node.name] = ast.unparse(node.args)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith('_'):
            api[node.name] = 'class'
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id.isupper()
                        and not target.id.startswith('_')):
                    api[target.id] = 'const'
    return api


def _normalized(signature: str) -> str:
    for binary in _BINARY_DEFAULTS:
        signature = signature.replace(f"'{binary}'", '<binary>')
    return signature


class ClientClassParityTests(unittest.TestCase):
    def test_the_same_public_methods(self) -> None:
        claude = {n for n, _ in inspect.getmembers(ClaudeCliClient, inspect.isfunction)
                  if not n.startswith('_')}
        codex = {n for n, _ in inspect.getmembers(CodexCliClient, inspect.isfunction)
                 if not n.startswith('_')}

        self.assertEqual(claude - codex, set(), 'only Claude has these')
        self.assertEqual(codex - claude, set(), 'only Codex has these')

    def test_the_same_public_signatures(self) -> None:
        for name in ('validate_connection', 'investigate', 'fix_review_comments',
                     'implement_task', 'test_task', 'fix_review_comment',
                     'delete_conversation', 'stop_all_conversations'):
            with self.subTest(method=name):
                self.assertEqual(
                    str(inspect.signature(getattr(ClaudeCliClient, name))),
                    str(inspect.signature(getattr(CodexCliClient, name))),
                )

    def test_the_same_constructor(self) -> None:
        claude = inspect.signature(ClaudeCliClient.__init__).parameters
        codex = inspect.signature(CodexCliClient.__init__).parameters

        self.assertEqual(list(claude), list(codex), 'constructor arguments differ')
        for name, param in claude.items():
            with self.subTest(argument=name):
                self.assertEqual(
                    param.default, codex[name].default,
                    f'{name} defaults differ between the transports',
                )

    def test_private_differences_are_declared(self) -> None:
        # A private method that exists on only one transport is fine — the
        # CLIs differ — but it must be listed above, so the next divergence
        # is a decision someone made rather than one nobody noticed.
        claude = {n for n, _ in inspect.getmembers(ClaudeCliClient, inspect.isfunction)
                  if n.startswith('_') and not n.startswith('__')}
        codex = {n for n, _ in inspect.getmembers(CodexCliClient, inspect.isfunction)
                 if n.startswith('_') and not n.startswith('__')}

        undeclared = (claude ^ codex) - _TRANSPORT_ONLY_METHODS
        self.assertEqual(
            undeclared, set(),
            'these exist on one transport only and are not declared as a real '
            f'CLI difference: {sorted(undeclared)}',
        )


class HelperModuleParityTests(unittest.TestCase):
    def test_every_transport_ships_the_shared_helpers(self) -> None:
        for lib in ('claude_core_lib', 'codex_core_lib'):
            for module in _SHARED_HELPER_MODULES:
                with self.subTest(lib=lib, module=module):
                    self.assertTrue(
                        (_REPO_ROOT / lib / lib / module).is_file(),
                        f'{lib} is missing {module}; a caller holding a backend '
                        'cannot ask it the same question as the others',
                    )

    def test_the_shared_helpers_expose_the_same_names(self) -> None:
        for module in _SHARED_HELPER_MODULES:
            with self.subTest(module=module):
                claude = _public_api(
                    _REPO_ROOT / 'claude_core_lib' / 'claude_core_lib' / module)
                codex = _public_api(
                    _REPO_ROOT / 'codex_core_lib' / 'codex_core_lib' / module)
                self.assertEqual(
                    set(claude), set(codex),
                    f'{module} exposes different names per transport',
                )

    def test_the_shared_helpers_have_the_same_signatures(self) -> None:
        # Only the transport's own binary name may differ.
        for module in _SHARED_HELPER_MODULES:
            claude = _public_api(
                _REPO_ROOT / 'claude_core_lib' / 'claude_core_lib' / module)
            codex = _public_api(
                _REPO_ROOT / 'codex_core_lib' / 'codex_core_lib' / module)
            for name in sorted(set(claude) & set(codex)):
                with self.subTest(module=module, name=name):
                    self.assertEqual(
                        _normalized(claude[name]), _normalized(codex[name]),
                        f'{module}:{name} has a different signature per transport',
                    )

    def test_model_discovery_is_callable_identically_on_both(self) -> None:
        # The failure this prevents: a backend whose discovery quietly takes
        # different arguments fails only at runtime, on the operator's picker.
        from claude_core_lib.claude_core_lib.helpers import model_catalog as claude
        from codex_core_lib.codex_core_lib.helpers import model_catalog as codex

        for module in (claude, codex):
            with self.subTest(module=module.__name__):
                self.assertTrue(module.discover_models(force=False))
                self.assertTrue(module.FALLBACK_MODELS)
                module.reset_models_cache()

    def test_effort_discovery_is_callable_identically_on_both(self) -> None:
        from claude_core_lib.claude_core_lib.helpers import effort_levels as claude
        from codex_core_lib.codex_core_lib.helpers import effort_levels as codex

        for module in (claude, codex):
            with self.subTest(module=module.__name__):
                self.assertTrue(module.FALLBACK_EFFORT_LEVELS)
                module.reset_effort_levels_cache()


if __name__ == '__main__':
    unittest.main()
