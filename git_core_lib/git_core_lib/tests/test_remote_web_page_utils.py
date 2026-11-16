"""A git remote's HTML error page is read as one line of what it says.

2026-11-16: a push after "merge master" failed with 106 lines omitted and then
the bottom of Bitbucket's error page — ``</div> </body> </html>`` — above
"(pre-receive hook declined)", which read like a rule refusing the push. The
page said the server had failed ("Something went wrong… check our status
page"); that is what is kept now.
"""

from __future__ import annotations

import unittest

from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from git_core_lib.git_core_lib.helpers.remote_web_page_utils import (
    PAGE_LINE_PREFIX,
    collapse_remote_web_pages,
)

# The shape a hosted server's error page takes down a push: every line after
# "remote:", git's trailing spaces included.
ERROR_PAGE = [
    'remote: <!DOCTYPE html>        ',
    'remote: <html lang="en">        ',
    'remote:   <head>        ',
    'remote:     <title>Something went wrong</title>        ',
    'remote:     <style>        ',
    *[f'remote:       .rule-{i} {{ margin: {i}px; }}        ' for i in range(80)],
    'remote:     </style>        ',
    'remote:   </head>        ',
    'remote:   <body>        ',
    'remote:     <div class="error">        ',
    'remote:       <svg viewBox="0 0 10 10">        ',
    'remote:         <path d="M0 0L10 10"/>        ',
    'remote:       </svg>        ',
    'remote:             <h1>Something went wrong.</h1>        ',
    'remote:             <p>        ',
    'remote:               While we check things on our end, try refreshing the page. If reloading the page doesn&#39;t help, check our        ',
    'remote:                 <a class="support-ga"        ',
    'remote:                   data-support-gaq-page="SiteStatus"        ',
    'remote:                   href="https://status.example.org/">status page</a>        ',
    'remote:               for known incidents or planned maintenance.        ',
    'remote:             </p>        ',
    'remote:     </div>        ',
    'remote:   </body>        ',
    'remote: </html>        ',
]
REJECTED = [
    'To example.org:team/admin-client.git',
    ' ! [remote rejected]     UNA-1 -> UNA-1 (pre-receive hook declined)',
    "error: failed to push some refs to 'example.org:team/admin-client.git'",
]
PAGE_TEXT = (
    f'{PAGE_LINE_PREFIX} Something went wrong. While we check things on our end, try refreshing '
    "the page. If reloading the page doesn't help, check our status page "
    '(https://status.example.org/) for known incidents or planned maintenance.'
)


class CollapseRemoteWebPagesTests(unittest.TestCase):
    def test_the_page_becomes_one_line_of_what_it_says_and_the_rest_is_kept(self) -> None:
        self.assertEqual(collapse_remote_web_pages([*ERROR_PAGE, *REJECTED]), [PAGE_TEXT, *REJECTED])

    def test_a_page_that_never_closes_ends_where_the_remote_stops(self) -> None:
        unclosed = ['remote: <html><body><p>Down for maintenance</p>', 'remote: </body>']
        self.assertEqual(
            collapse_remote_web_pages([*unclosed, *REJECTED]),
            [f'{PAGE_LINE_PREFIX} Down for maintenance', *REJECTED],
        )
        # ... or where the output stops.
        self.assertEqual(collapse_remote_web_pages(unclosed), [f'{PAGE_LINE_PREFIX} Down for maintenance'])

    def test_a_one_line_page_and_a_page_with_no_words(self) -> None:
        self.assertEqual(
            collapse_remote_web_pages(['remote: <html><body>Busy</body></html>', 'after']),
            [f'{PAGE_LINE_PREFIX} Busy', 'after'],
        )
        self.assertEqual(
            collapse_remote_web_pages(['remote: <html><head><title>x</title></head><body></body></html>']),
            [PAGE_LINE_PREFIX],
        )

    def test_ordinary_remote_messages_are_untouched(self) -> None:
        # A host's real refusal is a sentence, sometimes with a <placeholder>.
        lines = [
            'remote: error: GH006: Protected branch update failed for refs/heads/main.',
            'remote: Use <branch> instead.',
            *REJECTED,
        ]
        self.assertEqual(collapse_remote_web_pages(lines), lines)


class FailureMessageTests(unittest.TestCase):
    def test_the_push_failure_says_what_the_page_says_and_why_git_stopped(self) -> None:
        out = GitClientMixin._condense_git_output('\n'.join([*ERROR_PAGE, *REJECTED]))
        self.assertEqual(out.splitlines(), [PAGE_TEXT, *REJECTED])
        self.assertNotIn('omitted', out)
        self.assertNotIn('<', out.replace('(pre-receive', ''))


if __name__ == '__main__':
    unittest.main()
