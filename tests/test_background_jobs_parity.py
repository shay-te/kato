"""The server and the UI answer "is a background job still running?" the SAME way.

The server's ``is_working`` (claude_core_lib ``BackgroundJobs``) and the UI
reducer (``webserver/ui/src/utils/backgroundJobs.js``) each read the session's
events. When they disagreed, a tab looked busy that the server called idle, or
the other way round — and when the server got it wrong (UNA-3416) the host's
run waited out its idle limit and every review loop waited 2h for a chat that
had finished. Both now run ``tests/fixtures/background_jobs_cases.json``; this
test runs the JS one with node, the real file, no copy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.session.background_jobs import outstanding_background_jobs

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = REPO_ROOT / 'tests' / 'fixtures' / 'background_jobs_cases.json'
JS_MODULE = REPO_ROOT / 'webserver' / 'ui' / 'src' / 'utils' / 'backgroundJobs.js'

_NODE_RUNNER = """
import { readFileSync } from 'node:fs';
const { outstandingFromEvents } = await import(process.argv[1]);
const cases = JSON.parse(readFileSync(0, 'utf8')).cases;
process.stdout.write(JSON.stringify(cases.map((c) => outstandingFromEvents(c.events))));
"""


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding='utf-8'))['cases']


class BackgroundJobsParityTests(unittest.TestCase):
    def test_the_server_answers_every_case(self) -> None:
        for case in _cases():
            with self.subTest(case=case['name']):
                self.assertEqual(sorted(outstanding_background_jobs(case['events'])), case['outstanding'])

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_the_ui_answers_every_case_the_same(self) -> None:
        completed = subprocess.run(
            ['node', '--input-type=module', '-e', _NODE_RUNNER, JS_MODULE.as_uri()],
            input=CASES_PATH.read_text(encoding='utf-8'),
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        answers = json.loads(completed.stdout)
        for case, answer in zip(_cases(), answers, strict=True):
            with self.subTest(case=case['name']):
                self.assertEqual(answer, case['outstanding'])

    def test_the_cases_cover_the_report_kinds(self) -> None:
        text = CASES_PATH.read_text(encoding='utf-8')
        for kind in ('task_started', 'task_notification', 'task_updated',
                     'background_tasks_changed', '<task-notification>', 'run_in_background'):
            self.assertIn(kind, text)


if __name__ == '__main__':
    unittest.main()
