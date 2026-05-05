import unittest

from kato_core_lib.helpers.agent_prompt_utils import (
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    prepend_chat_workspace_context,
    prepend_forbidden_repository_guardrails,
    workspace_inventory_block,
)


class AgentPromptUtilsTests(unittest.TestCase):
    def test_ignored_repository_folder_names_parses_and_deduplicates(self) -> None:
        result = ignored_repository_folder_names(' alpha ,Beta,alpha,, gamma ')

        self.assertEqual(result, ['alpha', 'Beta', 'gamma'])

    def test_forbidden_repository_guardrails_names_out_of_bounds_tools(self) -> None:
        text = forbidden_repository_guardrails_text('secret-client, legacy-api')

        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', text)
        self.assertIn('- secret-client', text)
        self.assertIn('- legacy-api', text)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', text)
        self.assertIn('Execution protocol for forbidden repositories', text)

    def test_prepend_forbidden_repository_guardrails_returns_original_when_empty(self) -> None:
        self.assertEqual(prepend_forbidden_repository_guardrails('hello', ''), 'hello')

    def test_prepend_forbidden_repository_guardrails_adds_guardrails(self) -> None:
        result = prepend_forbidden_repository_guardrails('resume work', 'secret-client')

        self.assertTrue(result.startswith('Forbidden repository folders'))
        self.assertIn('secret-client', result)
        self.assertTrue(result.endswith('resume work'))

    def test_workspace_inventory_block_lists_cwd_and_extras_with_anchor_text(self) -> None:
        # Anchors Claude to the repos that EXIST in the workspace so
        # it can map operator shorthand ("the front end") onto a
        # real path instead of a name from the forbidden list.
        block = workspace_inventory_block(
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=[
                '/wks/UNA-2489/ob-love-admin-client',
                '/wks/UNA-2489/workflow-core-lib',
            ],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertIn('(cwd) /wks/UNA-2489/ob-love-admin-backend', block)
        self.assertIn('/wks/UNA-2489/ob-love-admin-client', block)
        self.assertIn('/wks/UNA-2489/workflow-core-lib', block)
        # The disambiguation sentence — the whole point of the
        # block — names the ``-new`` / ``-old`` failure mode so
        # Claude doesn't latch onto a similarly-named forbidden
        # repo when the workspace already has the right one.
        self.assertIn('-new', block)

    def test_workspace_inventory_block_returns_empty_when_no_paths(self) -> None:
        # No cwd, no extras → no block. Keeps prompts clean for
        # tasks that don't have a workspace yet (e.g. fresh task,
        # provisioning still in flight).
        self.assertEqual(workspace_inventory_block('', None), '')
        self.assertEqual(workspace_inventory_block('', []), '')

    def test_workspace_inventory_block_deduplicates_cwd_against_extras(self) -> None:
        # When a caller accidentally passes the cwd in
        # additional_dirs too, the block should not list the same
        # path twice — Claude already knows about its cwd.
        block = workspace_inventory_block(
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=[
                '/wks/UNA-2489/ob-love-admin-backend/',  # trailing slash duplicate
                '/wks/UNA-2489/ob-love-admin-client',
            ],
        )
        # Only one ``ob-love-admin-backend`` line.
        self.assertEqual(
            block.count('ob-love-admin-backend'),
            1,
        )

    def test_prepend_chat_workspace_context_orders_inventory_before_forbidden(self) -> None:
        # Inventory FIRST, forbidden SECOND, then the operator
        # message. Order matters: with inventory leading, the
        # forbidden block reads as "and don't go looking outside
        # this list" rather than "the frontend is forbidden".
        result = prepend_chat_workspace_context(
            'verify the front end',
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=['/wks/UNA-2489/ob-love-admin-client'],
            raw_ignored_value='ob-love-admin-client-new',
        )
        inventory_pos = result.find('Repositories available in this workspace:')
        forbidden_pos = result.find('Forbidden repository folders')
        message_pos = result.find('verify the front end')
        self.assertGreater(inventory_pos, -1)
        self.assertGreater(forbidden_pos, inventory_pos)
        self.assertGreater(message_pos, forbidden_pos)

    def test_prepend_chat_workspace_context_skips_blocks_when_empty(self) -> None:
        # No inventory + no forbidden config → original message
        # passes through untouched.
        self.assertEqual(
            prepend_chat_workspace_context('hello', cwd='', additional_dirs=None, raw_ignored_value=''),
            'hello',
        )


if __name__ == '__main__':
    unittest.main()
