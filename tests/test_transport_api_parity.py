"""Every CLI transport is a MIRROR of the others — the same files, names and signatures.

Kato picks a backend and then calls it. Every place that has to ask "which
backend is this?" before choosing a name or an argument is a place the
transports can silently diverge — and they did: for months one transport's
review prompt read a comment's line from a field the other didn't, and the bug
was fixed on one side only.

So this file locks the SHAPE of all four (Claude, Codex, Grok, OpenCode),
against the contract in ``agent_provider_contracts.cli_transport_layout``:

* the same files, and no module only one of them has unless it is declared
  below as a real CLI difference;
* the same public names in each module, the same signatures, and classes with
  the same members (the agent's own name substituted);
* client classes with identical public methods, signatures and constructors.

Where they legitimately differ (a default binary name, a CLI-specific private
helper, a capability only Claude's CLI has) the difference is named here, so
adding a new one is a deliberate edit rather than a silent drift.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import unittest
from pathlib import Path

from agent_provider_contracts.agent_provider_contracts.cli_transport_layout import (
    REQUIRED_FILES,
    REQUIRED_MEMBERS,
    REQUIRED_NAMES,
    REQUIRED_PARAMETERS,
    REQUIRED_SIGNATURES,
    SESSION_METADATA_FIELDS,
    for_agent,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: lib package → class-name stem.
TRANSPORTS = {
    'claude_core_lib': 'Claude',
    'codex_core_lib': 'Codex',
    'grok_core_lib': 'Grok',
    'opencode_core_lib': 'OpenCode',
}
#: The one-process-per-turn transports: built on the same shared base, so
#: between THEM nothing at all may differ but the CLI's own argv and events.
PER_TURN = ('codex_core_lib', 'grok_core_lib', 'opencode_core_lib')

#: Modules only one transport has, each because its CLI has a capability the
#: others lack. Anything else outside the contract's layout is drift.
_DECLARED_EXTRA_MODULES = {
    'claude_core_lib': {
        # Claude Code's own: an auth probe, the chat tool floor, context-window
        # sizes, the built-in file editor, model-id aliases, the price table,
        # Remote Control, result subtypes, the safeguard refusal, the spawn
        # argv for a persistent stream-json process, transcript usage, and the
        # per-spawn write-scope settings file.
        'helpers/auth_status.py', 'helpers/chat_floor.py', 'helpers/context_window.py',
        'helpers/file_editor_utils.py', 'helpers/model_ids.py', 'helpers/model_pricing.py',
        'helpers/remote_control.py', 'helpers/result_failure.py',
        'helpers/safeguard_error.py', 'helpers/spawn_utils.py',
        'helpers/transcript_usage.py', 'helpers/write_scope_settings.py',
        # The registry of live persistent processes (one per task).
        'session/registry.py',
        # Background jobs that outlive a turn (run_in_background / Monitor /
        # Workflow), read from the CLI's own task events: only a persistent
        # process has a turn that ends while its job still runs. A per-turn
        # CLI's process — and so its job — ends with the turn.
        'session/background_jobs.py',
    },
    'opencode_core_lib': {
        # OpenCode ships two maintained generations (V1 ``opencode-ai``, V2
        # ``@opencode/cli``) with different flags; which one is installed
        # decides ``--standalone``, the effort spelling and the export command.
        'helpers/cli_generation.py',
    },
}

# Private client methods only Claude has: a tool-permission model and a single
# JSON payload.
_CLAUDE_ONLY_METHODS = {
    '_extract_first_json_object', '_merge_allowed_with_read_only_allowlist',
    '_parse_json_payload', '_union_disallowed',
}
# Private client methods every per-turn transport has and Claude does not: a
# JSONL event stream translated into the shared vocabulary, knobs the CLI has
# no flag for, and a probe that the CLI can stream at all.
_PER_TURN_ONLY_METHODS = {
    '_knob_is_set', '_parse_jsonl_payload', '_smoke_test_failure',
    '_translate_wire_event', '_validate_streaming_support',
}

_BINARY_DEFAULTS = ('claude', 'codex', 'grok', 'opencode')


def _module(lib: str, dotted: str):
    return importlib.import_module(f'{lib}.{lib}' + (f'.{dotted}' if dotted else ''))


def _client(lib: str):
    return getattr(_module(lib, 'cli_client'), f'{TRANSPORTS[lib]}CliClient')


def _parameters(function) -> tuple[str, ...]:
    names: list[str] = []
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind is parameter.KEYWORD_ONLY and '*' not in names:
            names.append('*')
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        names.append(parameter.name)
    return tuple(names)


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


def _methods(cls, *, private: bool) -> set[str]:
    return {
        name for name, _ in inspect.getmembers(cls, inspect.isfunction)
        if name.startswith('_') == private and not name.startswith('__')
    }


class LayoutTests(unittest.TestCase):
    def test_every_transport_has_every_file(self) -> None:
        for lib in TRANSPORTS:
            for relative in REQUIRED_FILES:
                with self.subTest(lib=lib, file=relative):
                    self.assertTrue(
                        (_REPO_ROOT / lib / relative.replace('<pkg>', lib)).is_file(),
                        f'{lib} is missing {relative}',
                    )

    def test_no_transport_has_an_undeclared_module(self) -> None:
        required = {
            relative.replace('<pkg>/', '') for relative in REQUIRED_FILES
            if relative.startswith('<pkg>/')
        }
        for lib in TRANSPORTS:
            package = _REPO_ROOT / lib / lib
            present = {
                str(path.relative_to(package)) for path in package.rglob('*.py')
                if 'tests' not in path.relative_to(package).parts
            }
            with self.subTest(lib=lib):
                self.assertEqual(
                    present - required - _DECLARED_EXTRA_MODULES.get(lib, set()),
                    set(),
                    'a module only this transport has: mirror it in every '
                    'transport, or declare it as a real CLI difference',
                )

    def test_declared_extras_still_exist(self) -> None:
        for lib, extras in _DECLARED_EXTRA_MODULES.items():
            for relative in extras:
                with self.subTest(lib=lib, module=relative):
                    self.assertTrue((_REPO_ROOT / lib / lib / relative).is_file())


class PublicNameTests(unittest.TestCase):
    def test_every_module_defines_its_names(self) -> None:
        for lib, agent in TRANSPORTS.items():
            for dotted, names in REQUIRED_NAMES.items():
                module = _module(lib, dotted)
                for name in names:
                    with self.subTest(lib=lib, module=dotted, name=name):
                        self.assertTrue(hasattr(module, for_agent(name, agent)))

    def test_signatures_match_the_contract(self) -> None:
        for lib in TRANSPORTS:
            for path, expected in REQUIRED_SIGNATURES.items():
                dotted, _, name = path.rpartition('.')
                with self.subTest(lib=lib, function=path):
                    self.assertEqual(
                        _parameters(getattr(_module(lib, dotted), name)), expected,
                    )

    def test_required_parameters_are_taken(self) -> None:
        for lib in TRANSPORTS:
            for path, expected in REQUIRED_PARAMETERS.items():
                dotted, _, name = path.rpartition('.')
                taken = _parameters(getattr(_module(lib, dotted), name))
                with self.subTest(lib=lib, function=path):
                    self.assertEqual(taken[0], expected[0])
                    self.assertEqual(set(expected) - set(taken), set())

    def test_classes_have_their_members(self) -> None:
        homes = {
            '{Agent}CliClient': 'cli_client',
            'Streaming{Agent}Session': 'session.streaming',
            '{Agent}SessionManager': 'session.manager',
            'SessionEvent': 'session.streaming',
            '{Agent}SessionMetadata': 'session.index',
        }
        for lib, agent in TRANSPORTS.items():
            for class_name, members in REQUIRED_MEMBERS.items():
                cls = getattr(_module(lib, homes[class_name]), for_agent(class_name, agent))
                for member in members:
                    with self.subTest(lib=lib, cls=class_name, member=member):
                        self.assertTrue(
                            hasattr(cls, member)
                            or member in getattr(cls, '__dataclass_fields__', {}),
                        )

    def test_session_index_rows_carry_the_same_fields(self) -> None:
        for lib, agent in TRANSPORTS.items():
            metadata = getattr(_module(lib, 'session.index'), f'{agent}SessionMetadata')
            fields = {field.name for field in dataclasses.fields(metadata)}
            with self.subTest(lib=lib):
                self.assertEqual(set(SESSION_METADATA_FIELDS) - fields, set())

    def test_per_turn_wire_protocols_translate(self) -> None:
        for lib in PER_TURN:
            with self.subTest(lib=lib):
                self.assertTrue(callable(_module(lib, 'session.wire_protocol').translate_event))


class ClientClassParityTests(unittest.TestCase):
    def test_the_same_public_methods(self) -> None:
        reference = _methods(_client('claude_core_lib'), private=False)
        for lib in TRANSPORTS:
            with self.subTest(lib=lib):
                self.assertEqual(_methods(_client(lib), private=False), reference)

    def test_the_same_public_signatures(self) -> None:
        # Names, kinds and defaults — not annotation text: the per-turn base
        # lives in a lib that may not import the review-comment type, so it
        # writes ``list[Any]`` where Claude writes ``list[ReviewComment]``.
        def shape(function):
            return [
                (p.name, p.kind, p.default)
                for p in inspect.signature(function).parameters.values()
            ]

        reference = _client('claude_core_lib')
        for lib in TRANSPORTS:
            for name in ('validate_connection', 'investigate', 'fix_review_comments',
                         'implement_task', 'test_task', 'fix_review_comment',
                         'delete_conversation', 'stop_all_conversations'):
                with self.subTest(lib=lib, method=name):
                    self.assertEqual(
                        shape(getattr(reference, name)),
                        shape(getattr(_client(lib), name)),
                    )

    def test_the_same_constructor(self) -> None:
        reference = inspect.signature(_client('claude_core_lib').__init__).parameters
        for lib in TRANSPORTS:
            parameters = inspect.signature(_client(lib).__init__).parameters
            with self.subTest(lib=lib):
                self.assertEqual(list(parameters), list(reference))
            for name, parameter in reference.items():
                with self.subTest(lib=lib, argument=name):
                    self.assertEqual(parameter.default, parameters[name].default)

    def test_the_per_turn_transports_have_identical_private_methods(self) -> None:
        reference = _methods(_client(PER_TURN[0]), private=True)
        for lib in PER_TURN:
            with self.subTest(lib=lib):
                self.assertEqual(_methods(_client(lib), private=True), reference)

    def test_private_differences_from_claude_are_declared(self) -> None:
        claude = _methods(_client('claude_core_lib'), private=True)
        per_turn = _methods(_client(PER_TURN[0]), private=True)
        self.assertEqual(claude - per_turn, _CLAUDE_ONLY_METHODS)
        self.assertEqual(per_turn - claude, _PER_TURN_ONLY_METHODS)

    def test_every_client_names_its_cli(self) -> None:
        names = {_client(lib).CLI_DISPLAY_NAME for lib in TRANSPORTS}
        self.assertEqual(len(names), len(TRANSPORTS))
        self.assertNotIn('<unnamed CLI>', names)


class HelperModuleParityTests(unittest.TestCase):
    _HELPERS = ('helpers/model_catalog.py', 'helpers/effort_levels.py', 'helpers/one_shot_utils.py')

    def test_the_shared_helpers_expose_the_same_names_and_signatures(self) -> None:
        for module in self._HELPERS:
            reference = _public_api(_REPO_ROOT / 'claude_core_lib' / 'claude_core_lib' / module)
            for lib in TRANSPORTS:
                api = _public_api(_REPO_ROOT / lib / lib / module)
                with self.subTest(lib=lib, module=module):
                    self.assertEqual(set(api), set(reference))
                for name in sorted(set(api) & set(reference)):
                    with self.subTest(lib=lib, module=module, name=name):
                        self.assertEqual(_normalized(api[name]), _normalized(reference[name]))

    def test_model_and_effort_discovery_answer_identically(self) -> None:
        for lib in TRANSPORTS:
            models = _module(lib, 'helpers.model_catalog')
            efforts = _module(lib, 'helpers.effort_levels')
            with self.subTest(lib=lib):
                self.assertTrue(models.discover_models(force=False))
                self.assertTrue(models.FALLBACK_MODELS)
                self.assertIsInstance(models.discover_pinned_models(force=False), list)
                models.reset_models_cache()
                self.assertTrue(efforts.FALLBACK_EFFORT_LEVELS)
                efforts.reset_effort_levels_cache()


if __name__ == '__main__':
    unittest.main()
