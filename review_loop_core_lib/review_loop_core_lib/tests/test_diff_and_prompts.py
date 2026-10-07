"""What the reviewer is shown, and what the fixer is sent."""
from __future__ import annotations

import unittest

from review_loop_core_lib.review_loop_core_lib.diff import (
    candidate_digest,
    render_review_diff,
    split_file_diffs,
)
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    build_findings_prompt,
    findings_header,
)
from review_loop_core_lib.review_loop_core_lib.ports import LoopWording, RepoDiff
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    VERDICT_CLOSE,
    VERDICT_OPEN,
    build_reviewer_prompt,
)
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    WORDING,
    finding,
    reply,
    wrap,
)
from review_loop_core_lib.review_loop_core_lib.verdict import parse_review_verdict


def _file(path: str, body: str = '+x\n') -> str:
    return f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{body}'


class SplitTests(unittest.TestCase):

    def test_splits_per_file_in_order(self) -> None:
        chunks = split_file_diffs(_file('a.py') + _file('b/c.py', '+y\n+z\n'))
        self.assertEqual([path for path, _ in chunks], ['a.py', 'b/c.py'])
        self.assertTrue(chunks[1][1].startswith('diff --git a/b/c.py'))
        self.assertTrue(chunks[1][1].endswith('+z\n'))

    def test_no_diff_no_files(self) -> None:
        self.assertEqual(split_file_diffs(''), [])


class CandidateDigestTests(unittest.TestCase):

    def test_the_same_change_has_the_same_digest_in_any_repo_order(self) -> None:
        api = RepoDiff(repo_id='api', diff='diff --git a/x b/x\n+1')
        web = RepoDiff(repo_id='web', diff='diff --git a/y b/y\n+2')
        self.assertEqual(candidate_digest([api, web]), candidate_digest([web, api]))
        self.assertEqual(len(candidate_digest([api])), 64)

    def test_any_change_to_the_code_or_a_repo_error_changes_it(self) -> None:
        base = candidate_digest([RepoDiff(repo_id='api', diff='+1')])
        self.assertNotEqual(base, candidate_digest([RepoDiff(repo_id='api', diff='+2')]))
        self.assertNotEqual(base, candidate_digest([RepoDiff(repo_id='web', diff='+1')]))
        self.assertNotEqual(
            base, candidate_digest([RepoDiff(repo_id='api', diff='+1', error='no git')]),
        )
        # Parts are separated: moving text across a boundary is a change.
        self.assertNotEqual(
            candidate_digest([RepoDiff(repo_id='ap', diff='i+1')]), base,
        )

    def test_text_that_is_not_clean_unicode_still_hashes(self) -> None:
        self.assertEqual(len(candidate_digest([RepoDiff(repo_id='api', diff='\udcff')])), 64)


class RenderTests(unittest.TestCase):

    def test_every_repository_and_file_is_shown_when_it_fits(self) -> None:
        rendered = render_review_diff([
            RepoDiff(repo_id='api', diff=_file('a.py'), base='main', head='T-1', cwd='/w/api'),
            RepoDiff(repo_id='web', diff=_file('b.ts') + _file('c.ts')),
        ])
        self.assertEqual((rendered.repos, rendered.files, rendered.omitted_files), (2, 3, ()))
        self.assertIn('### Repository `api` (main → T-1) — 1 changed file(s)', rendered.text)
        self.assertIn('Path: /w/api', rendered.text)
        self.assertIn('### Repository `web` — 2 changed file(s)', rendered.text)
        self.assertNotIn('Not shown above', rendered.text)
        self.assertFalse(rendered.is_empty)

    def test_files_over_the_budget_are_listed_never_cut(self) -> None:
        big = _file('big.py', '+' + 'x' * 500 + '\n')
        rendered = render_review_diff(
            [RepoDiff(repo_id='api', diff=_file('small.py') + big + _file('tail.py'))],
            budget_chars=300,
        )
        self.assertEqual(rendered.omitted_files, ('api: big.py',))
        self.assertIn('diff --git a/small.py', rendered.text)
        self.assertIn('diff --git a/tail.py', rendered.text)
        self.assertNotIn('x' * 50, rendered.text)
        self.assertIn('read these in the repository yourself', rendered.text)
        self.assertIn('- api: big.py', rendered.text)

    def test_a_repository_that_could_not_be_read_says_so(self) -> None:
        rendered = render_review_diff([RepoDiff(repo_id='api', diff='', error='no base branch')])
        self.assertIn("Could not read this repository's diff: no base branch", rendered.text)
        self.assertTrue(rendered.is_empty)


class ReviewerPromptTests(unittest.TestCase):

    def _prompt(self, **overrides) -> str:
        diffs = [RepoDiff(repo_id='api', diff=_file('a.py'), cwd='/w/api')]
        values = dict(
            task_id='T-1', task_summary='Add login', task_description='Email + password.',
            diffs=diffs, rendered=render_review_diff(diffs), wording=WORDING,
        )
        values.update(overrides)
        return build_reviewer_prompt(**values)

    def test_report_only_with_the_verdict_contract(self) -> None:
        prompt = self._prompt()
        self.assertIn('Do not fix anything', prompt)
        self.assertIn(VERDICT_OPEN, prompt)
        self.assertIn(VERDICT_CLOSE, prompt)
        self.assertIn('correctness, security', prompt)

    def test_ticket_and_diff_are_framed_as_untrusted(self) -> None:
        prompt = self._prompt()
        self.assertIn('<untrusted source="task T-1 description">\nAdd login\n\nEmail + password.', prompt)
        self.assertIn('<untrusted source="task T-1 diff">', prompt)
        self.assertIn('- `api` at /w/api', prompt)
        self.assertIn('## Also\nRead the rules file first.', prompt)

    def test_no_ticket_text_and_no_guidance_leave_those_sections_out(self) -> None:
        bare = LoopWording(wrap_untrusted=wrap)
        prompt = self._prompt(task_summary='', task_description='', wording=bare)
        self.assertNotIn('description">', prompt)
        self.assertNotIn('## Also', prompt)
        self.assertIn('- `api` at /w/api', prompt)

    def test_the_example_verdict_in_the_prompt_is_itself_parseable(self) -> None:
        # The format the reviewer is shown must be one the parser accepts.
        verdict = parse_review_verdict(self._prompt())
        self.assertEqual(len(verdict.findings), 1)


class FindingsPromptTests(unittest.TestCase):

    def _prompt(self, wording=WORDING) -> str:
        verdict = parse_review_verdict(reply(
            finding('BLOCKER', title='SQL injection', symbol='search'),
            finding('MINOR', title='rename var', symbol='x'),
            finding('MAJOR', title='no test', file='', repo='', symbol=''),
        ))
        return build_findings_prompt(
            task_id='T-1', verdict=verdict, round_number=2, max_rounds=5, wording=wording,
        )

    def test_the_header_is_the_first_line(self) -> None:
        self.assertEqual(self._prompt().splitlines()[0], 'Host review loop — round 2 of 5')
        self.assertEqual(findings_header(WORDING, round_number=1, max_rounds=3),
                         'Host review loop — round 1 of 3')

    def test_only_blocking_findings_are_sent_framed_as_untrusted(self) -> None:
        prompt = self._prompt()
        self.assertIn('found 2 blocking issue(s)', prompt)
        self.assertIn('1. [BLOCKER] api/app.py:3 (search) — SQL injection\n   fix it', prompt)
        self.assertIn('2. [MAJOR] — no test', prompt)
        self.assertNotIn('rename var', prompt)
        self.assertIn('<untrusted source="task T-1 review findings, round 2">', prompt)

    def test_the_fixer_rules_and_host_guidance_follow(self) -> None:
        prompt = self._prompt()
        self.assertIn('Do not commit, push or run git', prompt)
        self.assertTrue(prompt.rstrip().endswith('Never print the done marker.'))
        generic = self._prompt(LoopWording(wrap_untrusted=wrap))
        self.assertEqual(generic.splitlines()[0], 'Review loop — round 2 of 5')
        self.assertTrue(generic.rstrip().endswith('not raised again without new evidence.'))

    def test_a_finding_without_a_line_shows_just_the_file(self) -> None:
        verdict = parse_review_verdict(reply({'severity': 'MAJOR', 'file': 'a.py', 'title': 't'}))
        prompt = build_findings_prompt(
            task_id='T-1', verdict=verdict, round_number=1, max_rounds=5, wording=WORDING,
        )
        self.assertIn('1. [MAJOR] a.py — t', prompt)


if __name__ == '__main__':
    unittest.main()
