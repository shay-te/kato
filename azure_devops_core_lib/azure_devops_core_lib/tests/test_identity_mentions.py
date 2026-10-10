"""Azure's ``@<GUID>`` mentions, rewritten into the readable brace form."""

from __future__ import annotations

import unittest

from azure_devops_core_lib.azure_devops_core_lib.helpers.identity_mentions import (
    identity_names,
    mentioned_identity_ids,
    rewrite_identity_mentions,
)

GUID = '6A2C8F1E-0F3B-4C6A-9E1D-3B7C2A9F0D11'


class IdentityMentionTests(unittest.TestCase):

    def test_mentions_are_found_with_or_without_braces_in_any_case(self) -> None:
        text = f'@<{GUID}> and @<{{{GUID.lower()}}}> but not @<not-a-guid> or a@<x>'
        self.assertEqual(mentioned_identity_ids(text), {GUID.lower()})
        self.assertEqual(mentioned_identity_ids(None), set())

    def test_names_prefer_the_sign_in_name_and_skip_what_cannot_be_used(self) -> None:
        names = identity_names([
            {'id': GUID, 'uniqueName': 'bot@example.com', 'displayName': 'Bot'},
            {'id': GUID, 'uniqueName': 'second@example.com'},   # first one wins
            {'id': 'B', 'displayName': 'Only A Display Name'},
            {'id': '', 'uniqueName': 'no id'},
            {'id': 'C'},                                         # no name
            'not an identity',
        ])
        self.assertEqual(names, {GUID.lower(): 'bot@example.com', 'b': 'Only A Display Name'})

    def test_known_and_unknown_mentions_are_rewritten(self) -> None:
        other = '11111111-2222-3333-4444-555555555555'
        self.assertEqual(
            rewrite_identity_mentions(
                f'@<{GUID}> fix this, cc @<{other}>', {GUID.lower(): 'bot@example.com'},
            ),
            f'@{{bot@example.com}} fix this, cc @{{{other}}}',
        )
        self.assertEqual(rewrite_identity_mentions(None, {}), '')


if __name__ == '__main__':
    unittest.main()
