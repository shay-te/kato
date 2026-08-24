"""Every call into the agent service must reach a method that exists.

``AgentService`` is a facade over six sub-services (``comments``,
``comment_runs``, ``publish``, ``repositories``, ``cleanup``, ``lessons``).
When a method moved onto one of them, a caller still saying
``service.push_task(...)`` does not fail any unit test — the tests construct
the sub-service directly, and the callers mock the facade. It fails at
runtime, and how loudly depends on luck:

* ``kato_core_lib.py`` wiring the done-callback crashed the whole boot with
  ``AttributeError: 'AgentService' object has no attribute
  finish_task_planning_session``;
* the webserver's merge finaliser sat inside a bare ``except Exception`` that
  only logged, so it would have failed silently on every diff read.

Both are real bugs this file exists to prevent. It checks two dispatch styles:
direct attribute access (here) and the webserver's dotted string names (in
``_resolve_agent_method``).
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
import unittest

from kato_core_lib.data_layers.service.agent_service import AgentService

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The namespaces on the facade, and the class behind each.
_SUBSYSTEMS = {
    'comments': ('task_comment_service', 'TaskCommentService'),
    'comment_runs': ('task_comment_run_service', 'TaskCommentRunService'),
    'publish': ('task_publish_service', 'TaskPublishService'),
    'repositories': ('task_repository_service', 'TaskRepositoryService'),
    'cleanup': ('task_cleanup_service', 'TaskCleanupService'),
    'lessons': ('task_lesson_service', 'TaskLessonService'),
}

# Variable names that hold the agent service across the codebase.
_HOLDER_NAMES = {'service', 'agent_service', '_service', '_agent_service'}

_SEARCH_ROOTS = ('kato_core_lib', 'webserver/kato_webserver')


def _subsystem_classes() -> dict[str, type]:
    out = {}
    for namespace, (module, class_name) in _SUBSYSTEMS.items():
        loaded = importlib.import_module(
            f'kato_core_lib.data_layers.service.{module}')
        out[namespace] = getattr(loaded, class_name)
    return out


def _methods_that_moved() -> dict[str, str]:
    """Public method name -> the namespace that owns it.

    Public only, deliberately. A private name like ``_session_manager`` exists
    as an INSTANCE attribute on the facade, which ``hasattr`` on the class
    cannot see — treating those as "moved" reports call sites that are
    perfectly correct.
    """
    moved: dict[str, str] = {}
    for namespace, cls in _subsystem_classes().items():
        for name in vars(cls):
            if (not name.startswith('_')
                    and not isinstance(vars(cls)[name], property)
                    and not hasattr(AgentService, name)):
                moved.setdefault(name, namespace)
    return moved


def _source_files():
    for root in _SEARCH_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob('*.py')):
            text = str(path)
            if '__pycache__' in text or '/tests/' in text:
                continue
            yield path


class DirectAttributeCallSiteTests(unittest.TestCase):
    def test_the_facade_still_exposes_every_namespace(self) -> None:
        for namespace in _SUBSYSTEMS:
            with self.subTest(namespace=namespace):
                self.assertTrue(
                    isinstance(getattr(AgentService, namespace, None), property),
                    f'AgentService.{namespace} is gone; every caller using it '
                    'is now broken at runtime',
                )

    def test_no_caller_asks_the_facade_for_a_moved_method(self) -> None:
        moved = _methods_that_moved()
        self.assertTrue(moved, 'no methods were found on the sub-services')
        stale = []
        for path in _source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr not in moved:
                    continue
                base = node.value
                holder = (base.id if isinstance(base, ast.Name)
                          else base.attr if isinstance(base, ast.Attribute) else '')
                if holder in _HOLDER_NAMES:
                    rel = path.relative_to(_REPO_ROOT)
                    stale.append(
                        f'{rel}:{node.lineno} {holder}.{node.attr} '
                        f'(should be {holder}.{moved[node.attr]}.{node.attr})'
                    )
        self.assertEqual(
            stale, [],
            'these call the facade for a method that lives on a sub-service; '
            'they fail at runtime, not here:\n  ' + '\n  '.join(stale),
        )


    def test_no_caller_getattrs_a_moved_method_off_the_facade(self) -> None:
        # The quietest form of this bug: ``getattr(service, 'x', None)``
        # returns None, the caller's ``if not callable(...)`` guard swallows
        # it, and the feature simply stops happening. That is exactly how the
        # review-comment teardown became a silent no-op.
        moved = _methods_that_moved()
        stale = []
        for path in _source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == 'getattr'
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in moved):
                    continue
                base = node.args[0]
                holder = (base.id if isinstance(base, ast.Name)
                          else base.attr if isinstance(base, ast.Attribute) else '')
                if holder in _HOLDER_NAMES:
                    method = node.args[1].value
                    rel = path.relative_to(_REPO_ROOT)
                    stale.append(
                        f'{rel}:{node.lineno} getattr({holder}, {method!r}) '
                        f'-> lives on {moved[method]}'
                    )
        self.assertEqual(
            stale, [],
            'these resolve to None at runtime and are then silently skipped:\n  '
            + '\n  '.join(stale),
        )


class StringDispatchCallSiteTests(unittest.TestCase):
    """The webserver resolves some methods by dotted string name."""

    def test_every_dotted_route_name_resolves(self) -> None:
        app_source = (_REPO_ROOT / 'webserver/kato_webserver/app.py').read_text()
        names = set(re.findall(r"_resolve_agent_method\(\s*app,\s*'([^']+)'", app_source))
        names |= set(re.findall(r"_agent_method\(\s*\w+,\s*'([^']+)'", app_source))
        self.assertTrue(names, 'no dispatched route names found — has the '
                               'resolver been renamed?')
        classes = _subsystem_classes()
        broken = []
        for name in sorted(names):
            if '.' in name:
                namespace, method = name.split('.', 1)
                holder = classes.get(namespace)
                if holder is None:
                    broken.append(f'{name} (unknown namespace {namespace!r})')
                elif not hasattr(holder, method):
                    broken.append(f'{name} ({holder.__name__} has no {method!r})')
            elif not hasattr(AgentService, name):
                broken.append(f'{name} (AgentService has no {name!r})')
        self.assertEqual(
            broken, [],
            'these route names resolve to nothing — the route answers 501 at '
            'runtime with no test failing:\n  ' + '\n  '.join(broken),
        )


if __name__ == '__main__':
    unittest.main()
