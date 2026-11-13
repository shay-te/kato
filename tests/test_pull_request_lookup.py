"""A branch's pull requests are asked of the git host through ONE lookup, rarely.

Real ``PullRequestLookup``, real ``RepositoryService`` (built from the test
config), real ``ReviewCommentService`` and ``AgentStateRegistry``, real
``requests`` errors; the only stand-in is the host's pull request endpoint,
which counts every call. No mocks.

The report: every 50s the review scan asked Bitbucket once per repository of
every task In Review whose ticket links no pull request — twenty calls a scan
for UNA-3211 — until Bitbucket answered nothing but 429.
"""

from __future__ import annotations

import ast
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import requests

from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.pull_request_lookup import (
    ANSWER_TTL_SECONDS,
    DEFAULT_PAUSE_SECONDS,
    MAX_PAUSE_SECONDS,
    LookupPaused,
    PullRequestLookup,
    host_of,
    rate_limit_pause_seconds,
)
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from tests.utils import build_test_cfg

REPO_ROOT = Path(__file__).resolve().parent.parent
BITBUCKET = 'https://api.bitbucket.org/2.0'
GITHUB = 'https://api.github.com'
SCAN_SECONDS = 50


def _repository(repository_id: str, host: str = BITBUCKET):
    return SimpleNamespace(id=repository_id, provider_base_url=host)


def _http_error(status: int, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers['Retry-After'] = retry_after
    return requests.HTTPError(f'{status} Client Error', response=response)


class _Clock(object):
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Host(object):
    """The git host's pull request endpoint: open pull requests per (repo,
    branch); every call counted; an error can be queued per repository."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.open: dict[tuple[str, str], list[dict]] = {}
        self.errors: dict[str, list[Exception]] = {}

    def ask(self, repository, *, source_branch, title_prefix, state):
        self.calls.append((repository.id, source_branch, state))
        queued = self.errors.get(repository.id)
        if queued:
            raise queued.pop(0)
        return [dict(pr) for pr in self.open.get((repository.id, source_branch), [])
                if str(pr.get('title', '')).startswith(title_prefix)]


class _Setup(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.host = _Host()
        self.lookup = PullRequestLookup(self.host.ask, clock=self.clock)


class RememberedAnswersTests(_Setup):
    def test_twenty_repositories_over_ten_scans_ask_twenty_times_not_two_hundred(self) -> None:
        repositories = [_repository(f'repo-{n}') for n in range(20)]
        for _scan in range(10):
            for repository in repositories:
                self.assertEqual(self.lookup.find(repository, source_branch='UNA-3211'), [])
            self.clock.advance(SCAN_SECONDS)
        self.assertEqual(len(self.host.calls), 20)

    def test_an_answer_is_asked_again_once_it_is_older_than_the_ttl(self) -> None:
        repository = _repository('api')
        self.lookup.find(repository, source_branch='UNA-1')
        self.clock.advance(ANSWER_TTL_SECONDS - 1)
        self.lookup.find(repository, source_branch='UNA-1')
        self.assertEqual(len(self.host.calls), 1)
        self.clock.advance(1)
        self.lookup.find(repository, source_branch='UNA-1')
        self.assertEqual(len(self.host.calls), 2)

    def test_a_pull_request_opened_outside_kato_is_found_within_the_ttl(self) -> None:
        repository = _repository('api')
        self.assertEqual(self.lookup.find(repository, source_branch='UNA-1'), [])
        self.host.open[('api', 'UNA-1')] = [{'id': '7', 'title': 'UNA-1 fix'}]
        self.clock.advance(ANSWER_TTL_SECONDS)
        self.assertEqual(self.lookup.find(repository, source_branch='UNA-1'), [{'id': '7', 'title': 'UNA-1 fix'}])

    def test_a_merged_pull_request_drops_out_once_its_answer_expires(self) -> None:
        repository = _repository('api')
        self.host.open[('api', 'UNA-1')] = [{'id': '7', 'title': 'UNA-1 fix'}]
        self.assertEqual(len(self.lookup.find(repository, source_branch='UNA-1')), 1)
        self.host.open.clear()  # merged: no longer open
        self.clock.advance(ANSWER_TTL_SECONDS)
        self.assertEqual(self.lookup.find(repository, source_branch='UNA-1'), [])

    def test_forget_makes_the_next_lookup_ask_and_leaves_other_branches_alone(self) -> None:
        api, web = _repository('api'), _repository('web')
        self.lookup.find(api, source_branch='UNA-1', title_prefix='UNA-1 ')
        self.lookup.find(api, source_branch='UNA-1', state='all')
        self.lookup.find(api, source_branch='UNA-2')
        self.lookup.find(web, source_branch='UNA-1')
        self.host.open[('api', 'UNA-1')] = [{'id': '9', 'title': 'UNA-1 x'}]
        self.lookup.forget(SimpleNamespace(id='API'), ' UNA-1 ')
        self.assertEqual(len(self.lookup.find(api, source_branch='UNA-1', title_prefix='UNA-1 ')), 1)
        self.lookup.find(api, source_branch='UNA-2')
        self.lookup.find(web, source_branch='UNA-1')
        self.assertEqual(len(self.host.calls), 5)  # only the forgotten branch was asked again

    def test_each_title_prefix_and_state_is_its_own_answer(self) -> None:
        repository = _repository('api')
        self.lookup.find(repository, source_branch='UNA-1', title_prefix='UNA-1 ')
        self.lookup.find(repository, source_branch='UNA-1', title_prefix='')
        self.lookup.find(repository, source_branch='UNA-1', state='ALL')
        self.lookup.find(repository, source_branch='UNA-1', state='all')
        self.assertEqual(len(self.host.calls), 3)

    def test_what_is_returned_is_a_copy(self) -> None:
        repository = _repository('api')
        self.host.open[('api', 'UNA-1')] = [{'id': '7', 'title': 'UNA-1 fix'}]
        self.lookup.find(repository, source_branch='UNA-1')[0]['id'] = 'changed'
        self.assertEqual(self.lookup.find(repository, source_branch='UNA-1')[0]['id'], '7')

    def test_not_cached_always_asks(self) -> None:
        repository = _repository('api')
        self.lookup.find(repository, source_branch='UNA-1')
        self.lookup.find(repository, source_branch='UNA-1', cached=False)
        self.assertEqual(len(self.host.calls), 2)

    def test_none_from_the_host_is_no_pull_request(self) -> None:
        lookup = PullRequestLookup(lambda repository, **kwargs: None, clock=self.clock)
        self.assertEqual(lookup.find(_repository('api'), source_branch='UNA-1'), [])


class RateLimitPauseTests(_Setup):
    def test_a_429_pauses_every_lookup_on_that_host_until_retry_after(self) -> None:
        first, second = _repository('repo-1'), _repository('repo-2')
        self.host.errors['repo-1'] = [_http_error(429, retry_after='30')]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(first, source_branch='UNA-1')
        with self.assertRaisesRegex(LookupPaused, 'asked kato to slow down'):
            self.lookup.find(second, source_branch='UNA-1')  # never fired into the limit
        self.assertEqual(len(self.host.calls), 1)
        self.assertEqual(self.lookup.paused_for(second), 30)
        self.clock.advance(30)
        self.assertEqual(self.lookup.paused_for(second), 0)
        self.assertEqual(self.lookup.find(second, source_branch='UNA-1'), [])
        self.assertEqual(len(self.host.calls), 2)

    def test_another_host_is_not_paused(self) -> None:
        self.host.errors['repo-1'] = [_http_error(429)]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(_repository('repo-1'), source_branch='UNA-1')
        self.assertEqual(self.lookup.find(_repository('gh', GITHUB), source_branch='UNA-1'), [])

    def test_while_paused_an_old_answer_is_used_never_none(self) -> None:
        repository = _repository('api')
        self.host.open[('api', 'UNA-1')] = [{'id': '7', 'title': 'UNA-1 fix'}]
        self.lookup.find(repository, source_branch='UNA-1')
        self.clock.advance(ANSWER_TTL_SECONDS + 1)  # expired
        self.host.errors['other'] = [_http_error(429, retry_after='60')]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(_repository('other'), source_branch='UNA-1')
        self.assertEqual(self.lookup.find(repository, source_branch='UNA-1'), [{'id': '7', 'title': 'UNA-1 fix'}])

    def test_not_cached_is_still_never_asked_while_paused(self) -> None:
        repository = _repository('api')
        self.lookup.find(repository, source_branch='UNA-1')
        self.host.errors['other'] = [_http_error(429, retry_after='60')]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(_repository('other'), source_branch='UNA-1')
        with self.assertRaises(LookupPaused):
            self.lookup.find(repository, source_branch='UNA-1', cached=False)

    def test_any_other_error_pauses_nothing(self) -> None:
        self.host.errors['api'] = [_http_error(500), RuntimeError('network down')]
        for _attempt in range(2):
            with self.assertRaises(Exception):
                self.lookup.find(_repository('api'), source_branch='UNA-1')
        self.assertEqual(self.lookup.find(_repository('api'), source_branch='UNA-1'), [])
        self.assertEqual(len(self.host.calls), 3)

    def test_a_longer_pause_is_never_shortened_by_a_later_429(self) -> None:
        self.host.errors['repo-1'] = [_http_error(429, retry_after='300')]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(_repository('repo-1'), source_branch='UNA-1')
        lookup_ask = self.lookup._ask
        self.lookup._ask = lambda *a, **k: (_ for _ in ()).throw(_http_error(429, retry_after='5'))
        self.clock.advance(1)
        with self.assertRaises(LookupPaused):
            self.lookup.find(_repository('repo-2'), source_branch='UNA-1')
        self.lookup._ask = lookup_ask
        self.assertEqual(self.lookup.paused_for(_repository('x')), 299)

    def test_the_paused_message_names_the_host_and_the_wait(self) -> None:
        self.assertEqual(
            str(LookupPaused(BITBUCKET, 41.5)),
            f'{BITBUCKET} asked kato to slow down (HTTP 429); not asking it again for 42s',
        )
        self.assertIn('the git host asked', str(LookupPaused('', -3)))
        self.assertEqual(LookupPaused('', -3).seconds_left, 0)


class PauseSecondsTests(unittest.TestCase):
    def test_retry_after_is_honoured_and_clamped(self) -> None:
        self.assertEqual(rate_limit_pause_seconds(_http_error(429, '17')), 17)
        self.assertEqual(rate_limit_pause_seconds(_http_error(429, '99999')), MAX_PAUSE_SECONDS)

    def test_no_or_unusable_retry_after_is_the_default(self) -> None:
        for retry_after in (None, '0', '-4', 'Wed, 21 Oct 2026 07:28:00 GMT'):
            with self.subTest(retry_after=retry_after):
                self.assertEqual(rate_limit_pause_seconds(_http_error(429, retry_after)), DEFAULT_PAUSE_SECONDS)

    def test_a_429_wrapped_in_another_error_is_still_a_429(self) -> None:
        try:
            try:
                raise _http_error(429, '20')
            except requests.HTTPError as inner:
                raise RuntimeError('lookup failed') from inner
        except RuntimeError as outer:
            self.assertEqual(rate_limit_pause_seconds(outer), 20)

    def test_anything_else_is_no_pause(self) -> None:
        self.assertEqual(rate_limit_pause_seconds(_http_error(500)), 0)
        self.assertEqual(rate_limit_pause_seconds(RuntimeError('x')), 0)
        looped = RuntimeError('loop')
        looped.__context__ = looped
        self.assertEqual(rate_limit_pause_seconds(looped), 0)

    def test_the_host_is_the_pull_request_api_address(self) -> None:
        self.assertEqual(host_of(_repository('x', ' https://API.bitbucket.org/2.0 ')), 'https://api.bitbucket.org/2.0')
        self.assertEqual(host_of(SimpleNamespace(id='x')), '')


class RepositoryServiceTests(unittest.TestCase):
    """The real ``RepositoryService`` funnels both lookups through the one lookup."""

    def setUp(self) -> None:
        self.service = RepositoryService(build_test_cfg(), 3)
        self.host = _Host()
        self.clock = _Clock()
        self.service.__dict__['_pull_request_lookup'] = PullRequestLookup(
            self.host.ask, clock=self.clock,
        )
        self.service._publication_service._ensure_pr_api_or_log_once = lambda repository, operation: True
        self.repository = _repository('client')

    def test_the_lookup_is_made_once_per_service_and_asks_the_pull_request_api(self) -> None:
        fresh = RepositoryService(build_test_cfg(), 3)
        lookup = fresh.pull_request_lookup
        self.assertIs(fresh.pull_request_lookup, lookup)
        fresh._publication_service._ensure_pr_api_or_log_once = lambda repository, operation: True
        asked = []

        class _PullRequestApi(object):
            def find_pull_requests(self, **kwargs):
                asked.append(kwargs)
                return []

        fresh._pull_request_data_access = lambda repository: _PullRequestApi()
        fresh.find_pull_requests(self.repository, source_branch='UNA-1', title_prefix='UNA-1 ')
        fresh.find_pull_requests(self.repository, source_branch='UNA-1', title_prefix='UNA-1 ')
        fresh.find_pull_requests(self.repository, source_branch='UNA-1', state='all')
        self.assertEqual(asked, [
            {'source_branch': 'UNA-1', 'title_prefix': 'UNA-1 ', 'state': 'open'},
            {'source_branch': 'UNA-1', 'state': 'all'},
        ])

    def test_no_pull_request_api_is_no_pull_requests_without_asking(self) -> None:
        self.service._publication_service._ensure_pr_api_or_log_once = lambda repository, operation: False
        self.assertEqual(self.service.find_pull_requests(self.repository, source_branch='UNA-1'), [])
        self.assertEqual(self.host.calls, [])

    def test_find_is_remembered_and_not_cached_asks(self) -> None:
        self.service.find_pull_requests(self.repository, source_branch='UNA-1')
        self.service.find_pull_requests(self.repository, source_branch='UNA-1')
        self.service.find_pull_requests(self.repository, source_branch='UNA-1', cached=False)
        self.assertEqual(len(self.host.calls), 2)

    def test_states_always_ask_and_raise_while_the_host_is_paused(self) -> None:
        self.service._prepare_pull_request_api = lambda repository: None
        self.service.pull_request_states(self.repository, source_branch='UNA-1')
        self.service.pull_request_states(self.repository, source_branch='UNA-1')
        self.assertEqual(self.host.calls, [('client', 'UNA-1', 'all')] * 2)
        self.host.errors['client'] = [_http_error(429, '60')]
        with self.assertRaises(requests.HTTPError):
            self.service.pull_request_states(self.repository, source_branch='UNA-1')
        with self.assertRaises(LookupPaused):
            self.service.pull_request_states(self.repository, source_branch='UNA-1')

    def test_a_pull_request_kato_opens_is_found_at_once(self) -> None:
        self.assertEqual(self.service.find_pull_requests(self.repository, source_branch='UNA-1'), [])
        opened = {'id': '31', 'title': 'UNA-1 fix', 'url': 'https://host/pr/31'}

        def create(repository, title, source_branch, **kwargs):
            self.host.open[(repository.id, source_branch)] = [opened]
            return dict(opened)

        self.service._publication_service.create_pull_request = create
        self.assertEqual(self.service.create_pull_request(self.repository, 'UNA-1 fix', 'UNA-1'), opened)
        self.assertEqual(self.service.find_pull_requests(self.repository, source_branch='UNA-1'), [opened])


class _Tasks(object):
    def __init__(self, tasks) -> None:
        self._tasks = tasks

    def get_review_tasks(self):
        return list(self._tasks)


class _Repositories(object):
    """The repository service's task-facing half; the lookup is the real one."""

    def __init__(self, repositories, lookup: PullRequestLookup) -> None:
        self._repositories = repositories
        self._lookup = lookup

    def resolve_task_repositories(self, task):
        return list(self._repositories)

    def build_branch_name(self, task, repository):
        return str(task.id)

    def find_pull_requests(self, repository, *, source_branch, title_prefix):
        return self._lookup.find(repository, source_branch=source_branch, title_prefix=title_prefix)


class _MustNotRun(object):
    def __getattr__(self, attribute):
        raise AssertionError(f'{attribute} was used')


class ReviewScanTests(_Setup):
    """The real review scan over the real lookup."""

    def _scan_service(self, repositories, tasks):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.logger = logging.getLogger(f'test_pull_request_lookup.{self.id()}')
        return ReviewCommentService(
            _Tasks(tasks), _MustNotRun(), _Repositories(repositories, self.lookup),
            AgentStateRegistry(Path(tmp.name) / 'processed.json'), logger=self.logger,
        )

    @staticmethod
    def _task(task_id='UNA-3211'):
        return SimpleNamespace(id=task_id, summary='many repositories', description='')

    def test_ten_scans_of_a_twenty_repository_task_ask_the_host_twenty_times(self) -> None:
        repositories = [_repository(f'repo-{n}') for n in range(20)]
        self.host.open[('repo-3', 'UNA-3211')] = [{'id': '5', 'title': 'UNA-3211 fix'}]
        service = self._scan_service(repositories, [self._task()])
        for _scan in range(10):
            contexts = service._review_pull_request_contexts()
            # The one with a pull request is polled for comments EVERY scan.
            self.assertEqual([(c['id'], c['repository_id']) for c in contexts], [('5', 'repo-3')])
            self.clock.advance(SCAN_SECONDS)
        self.assertEqual(len(self.host.calls), 20)  # it was 200

    def test_a_host_error_is_one_warning_and_the_other_repositories_still_count(self) -> None:
        repositories = [_repository('broken'), _repository('fine')]
        self.host.errors['broken'] = [_http_error(500)]
        self.host.open[('fine', 'UNA-3211')] = [{'id': '8', 'title': 'UNA-3211 x'}]
        service = self._scan_service(repositories, [self._task()])
        with self.assertLogs(self.logger, level='WARNING') as logs:
            contexts = service._review_pull_request_contexts()
        self.assertEqual([c['repository_id'] for c in contexts], ['fine'])
        self.assertEqual(len(logs.output), 1)
        self.assertIn('could not look up the pull requests of task UNA-3211 in broken', logs.output[0])
        self.assertNotIn('Traceback', '\n'.join(logs.output))

    def test_a_429_stops_the_scan_firing_the_rest_into_the_limit_and_says_so_once(self) -> None:
        repositories = [_repository(f'repo-{n}') for n in range(20)]
        self.host.errors['repo-0'] = [_http_error(429, '90')]
        service = self._scan_service(repositories, [self._task()])
        with self.assertLogs(self.logger, level='WARNING') as logs:
            self.assertEqual(service._review_pull_request_contexts(), [])
        self.assertEqual(len(self.host.calls), 1)
        self.assertEqual(len(logs.output), 2)  # the 429 itself, then one summary
        self.assertIn('19 repositories were not checked for pull requests', logs.output[1])
        self.assertIn('not asking it again for 90s', logs.output[1])
        self.clock.advance(90)
        service._review_pull_request_contexts()
        self.assertEqual(len(self.host.calls), 21)  # the 429'd one asked again, the 19 for the first time

    def test_a_paused_lookup_before_any_scan_started_is_still_counted(self) -> None:
        service = self._scan_service([], [])
        self.host.errors['a'] = [_http_error(429, '30')]
        with self.assertRaises(requests.HTTPError):
            self.lookup.find(_repository('a'), source_branch='UNA-1')
        self.assertEqual(service._looked_up_pull_requests(self._task(), _repository('b'), 'UNA-1', ''), [])
        self.assertEqual(len(service._paused_lookups), 1)


#: Only these (file, function) pairs may ask the host past the lookup.
_ALLOWED_HOST_CALLS = {
    ('kato_core_lib/data_layers/service/repository_service.py', '_ask_host_for_pull_requests'),
    ('kato_core_lib/data_layers/data_access/pull_request_data_access.py', 'find_pull_requests'),
}
_HOST_RECEIVERS = ('_publication_service', '_pull_request_data_access', '_client', 'client')


def _host_calls_past_the_lookup() -> list[str]:
    found = []
    for root in ('kato_core_lib', 'webserver/kato_webserver'):
        for path in sorted((REPO_ROOT / root).rglob('*.py')):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if '/tests/' in f'/{relative}':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if _asks_the_host(node) and (relative, function.name) not in _ALLOWED_HOST_CALLS:
                        found.append(f'{relative}:{node.lineno} in {function.name}')
    return found


def _asks_the_host(node) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'find_pull_requests'):
        return False
    receiver = ast.unparse(node.func.value)
    return any(name in receiver for name in _HOST_RECEIVERS)


class SingleLookupGateTests(unittest.TestCase):
    def test_nothing_asks_the_host_for_pull_requests_but_the_lookup(self) -> None:
        self.assertEqual(
            _host_calls_past_the_lookup(), [],
            'ask for pull requests through RepositoryService.find_pull_requests / '
            'pull_request_states (PullRequestLookup), never the publication service or data access',
        )

    def test_the_gate_sees_a_direct_call(self) -> None:
        direct = ast.parse('self._repository_service._pull_request_data_access(r).find_pull_requests()')
        self.assertTrue(_asks_the_host(direct.body[0].value))
        funnel = ast.parse('self._repository_service.find_pull_requests(r)')
        self.assertFalse(_asks_the_host(funnel.body[0].value))

    def test_the_allowed_places_exist(self) -> None:
        for relative, _function in _ALLOWED_HOST_CALLS:
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)


if __name__ == '__main__':
    unittest.main()
