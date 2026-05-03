import unittest

from kato_core_lib.helpers.agent_prompt_utils import (
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    prepend_forbidden_repository_guardrails,
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


if __name__ == '__main__':
    unittest.main()
