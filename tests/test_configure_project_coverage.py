"""Coverage for ``configure_project`` defensive branches + IO loops."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib import configure_project as cp


class CoreHelperImportFallbackTests(unittest.TestCase):
    """Lines 30-33: ImportError fallback when core_lib helpers absent."""

    def test_local_input_helpers_used_when_core_helpers_unavailable(self) -> None:
        # Drive the fallback path by setting the module-level helpers to None.
        with patch.object(cp, 'core_input_string', None), \
             patch.object(cp, 'core_input_yes_no', None), \
             patch.object(cp, 'core_is_int', None):
            # input_yes_no goes through _input_yes_no_local.
            with patch.object(cp, '_input_yes_no_local', return_value=True) as fake:
                self.assertTrue(cp.input_yes_no('proceed?', default=True))
                fake.assert_called_once()
            # _is_int uses the local try/except path.
            self.assertTrue(cp._is_int('42'))
            self.assertFalse(cp._is_int('not-a-number'))


class CoreInputStringBranchTests(unittest.TestCase):
    def test_uses_core_input_string_when_available_and_default_none(self) -> None:
        # Line 169: ``core_input_string is not None and default is None``.
        with patch.object(cp, 'core_input_string', MagicMock(return_value='value')):
            result = cp.input_str('msg')
        self.assertEqual(result, 'value')


class InputIntDefaultsTests(unittest.TestCase):
    def test_returns_default_when_blank_input(self) -> None:
        # Line 182.
        with patch.object(cp, '_input_str_local', return_value=''):
            self.assertEqual(cp.input_int('how many?', default=7), 7)

    def test_loops_until_valid_integer_entered(self) -> None:
        # Lines 184-185: prompts again on non-integer input.
        responses = iter(['abc', '5'])
        with patch.object(cp, '_input_str_local',
                          side_effect=lambda *a, **kw: next(responses)), \
             patch.object(cp.logger, 'info') as mock_info:
            self.assertEqual(cp.input_int('how many?'), 5)
        # The "please enter a valid integer" message was logged.
        msgs = [str(c.args[0]) for c in mock_info.call_args_list]
        self.assertTrue(any('valid integer' in m for m in msgs))


class BuildConfigurationValuesClaudeBranchTests(unittest.TestCase):
    def test_branches_into_claude_backend_path(self) -> None:
        # Line 295: ``if agent_backend == 'claude': values.update(_prompt_claude_backend...)``.
        with patch.object(cp, 'input_enum', side_effect=[
            'youtrack',  # issue platform
            'claude',    # agent backend
        ]), patch.object(cp, '_prompt_issue_platform', return_value={}), \
             patch.object(cp, '_prompt_repository', return_value={}), \
             patch.object(cp, '_prompt_claude_backend', return_value={'KEY': 'val'}) as claude_prompt, \
             patch.object(cp, '_prompt_openhands') as oh_prompt, \
             patch.object(cp, '_prompt_notifications', return_value={}):
            cp.build_configuration_values({})
        claude_prompt.assert_called_once()
        oh_prompt.assert_not_called()


class PromptBypassPermissionsTests(unittest.TestCase):
    """Lines 1004-1034: bypass-permissions interactive flow."""

    def test_returns_false_when_user_declines(self) -> None:
        # Lines 1012-1013.
        with patch.object(cp, 'input_bool', return_value=False):
            result = cp._prompt_bypass_permissions({})
        self.assertEqual(result, {'KATO_CLAUDE_BYPASS_PERMISSIONS': 'false'})

    def test_returns_false_when_confirmation_phrase_wrong(self) -> None:
        # Lines 1014-1033: chose_bypass=True but confirmation != 'I ACCEPT'.
        with patch.object(cp, 'input_bool', return_value=True), \
             patch.object(cp, 'input_str', return_value='I refuse'), \
             patch('builtins.print'):
            result = cp._prompt_bypass_permissions({})
        self.assertEqual(result, {'KATO_CLAUDE_BYPASS_PERMISSIONS': 'false'})

    def test_returns_true_when_confirmation_phrase_correct(self) -> None:
        # Lines 1034: ``'I ACCEPT'`` confirms.
        with patch.object(cp, 'input_bool', return_value=True), \
             patch.object(cp, 'input_str', return_value='I ACCEPT'), \
             patch('builtins.print'):
            result = cp._prompt_bypass_permissions({})
        self.assertEqual(result, {'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true'})


class WriteConfigurationFileTests(unittest.TestCase):
    def test_writes_rendered_env_to_disk(self) -> None:
        # Lines 425-435 + 442: write file + report validation.
        with tempfile.TemporaryDirectory() as td:
            template = Path(td) / 'template.env'
            template.write_text('KATO_FOO=bar\n', encoding='utf-8')
            output = Path(td) / '.env'
            with patch.object(cp, 'build_configuration_values',
                              return_value={'KATO_FOO': 'baz'}):
                rc = cp._write_configuration_file(template, output, {})
            content = output.read_text(encoding='utf-8')
        self.assertEqual(rc, 0)
        self.assertIn('KATO_FOO=baz', content)


class ReportConfigurationValidationTests(unittest.TestCase):
    def test_reports_validation_errors(self) -> None:
        # Lines 449-451.
        with patch.object(cp, 'validate_agent_env',
                          return_value=['ERR-A']), \
             patch.object(cp, 'validate_openhands_env',
                          return_value=['ERR-B']), \
             patch.object(cp.logger, 'info') as mock_info:
            rc = cp._report_configuration_validation({
                'KATO_AGENT_BACKEND': 'openhands',
            })
        # Always returns 0 even with errors (file was written).
        self.assertEqual(rc, 0)
        # The errors are passed as positional %s args to logger.info.
        all_args = []
        for call in mock_info.call_args_list:
            all_args.extend(str(a) for a in call.args)
        self.assertIn('ERR-A', all_args)
        self.assertIn('ERR-B', all_args)


class RenderEnvTextElseBranchTests(unittest.TestCase):
    """Line 368: ``else: lines.append(line)`` — lines not matching a
    KEY=VALUE pattern are preserved verbatim."""

    def test_preserves_comments_and_blank_lines(self) -> None:
        template = '# header comment\n\nKATO_FOO=original\n# trailing comment\n'
        rendered = cp.render_env_text(template, {'KATO_FOO': 'updated'})
        self.assertIn('# header comment', rendered)
        self.assertIn('# trailing comment', rendered)
        self.assertIn('KATO_FOO=updated', rendered)

    def test_preserves_template_key_when_value_not_in_dict(self) -> None:
        # Line 368: ``else: lines.append(line)`` — key in template but
        # NOT in values dict. The original line is preserved verbatim.
        template = 'KATO_FOO=original\nKATO_BAR=other\n'
        rendered = cp.render_env_text(template, {'KATO_FOO': 'updated'})
        # KATO_BAR not in values → preserved as-is.
        self.assertIn('KATO_BAR=other', rendered)
        self.assertIn('KATO_FOO=updated', rendered)


class InputYesNoCoreHelperTests(unittest.TestCase):
    """Line 155: ``core_input_yes_no`` returns truthy → bool wrapper."""

    def test_uses_core_helper_when_available(self) -> None:
        with patch.object(cp, 'core_input_yes_no',
                          MagicMock(return_value=True)):
            self.assertTrue(cp.input_yes_no('proceed?', default=False))


class PromptClaudeBackendTests(unittest.TestCase):
    """Lines 303-307: logger.info messages + returned dict shape."""

    def test_emits_logger_messages_and_returns_keys(self) -> None:
        # We mock every input prompt to a stub value.
        with patch.object(cp, 'input_str', side_effect=[
            'claude',         # binary
            'haiku',          # model
            '',               # max_turns
            '',               # allowed tools
            '',               # disallowed tools
        ]), patch.object(cp, 'input_int', return_value=1800), \
             patch.object(cp, 'input_bool', side_effect=[False]), \
             patch.object(cp, '_prompt_bypass_permissions',
                          return_value={'KATO_CLAUDE_BYPASS_PERMISSIONS': 'false'}):
            result = cp._prompt_claude_backend({})
        self.assertIn('KATO_CLAUDE_BINARY', result)
        self.assertEqual(result['KATO_CLAUDE_BINARY'], 'claude')


class ReportConfigurationValidationClaudeBranchTests(unittest.TestCase):
    """Line 442: ``validate_claude_env`` branch — KATO_AGENT_BACKEND=claude."""

    def test_uses_claude_validator_when_backend_is_claude(self) -> None:
        with patch.object(cp, 'validate_agent_env', return_value=[]), \
             patch.object(cp, 'validate_claude_env',
                          return_value=['CLAUDE-ERR']) as claude_validator, \
             patch.object(cp, 'validate_openhands_env') as oh_validator, \
             patch.object(cp.logger, 'info'):
            cp._report_configuration_validation({
                'KATO_AGENT_BACKEND': 'claude',
            })
        claude_validator.assert_called_once()
        oh_validator.assert_not_called()


class DefaultProjectsRootTests(unittest.TestCase):
    """Line 1040: returns the configured REPOSITORY_ROOT_PATH normalized."""

    def test_returns_configured_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                'os.environ', {'REPOSITORY_ROOT_PATH': td}, clear=False,
            ):
                # _default_projects_root reads from values dict, not env.
                result = cp._default_projects_root({
                    'REPOSITORY_ROOT_PATH': td,
                })
                self.assertEqual(result, str(Path(td).resolve()))

    def test_returns_cwd_when_no_repository_root(self) -> None:
        result = cp._default_projects_root({})
        self.assertEqual(result, str(Path.cwd()))


class MainEntryPointTests(unittest.TestCase):
    def test_main_cancelled_when_user_declines_overwrite(self) -> None:
        # Lines 393-395.
        with tempfile.TemporaryDirectory() as td:
            template = Path(td) / 'template.env'
            template.write_text('KATO_FOO=bar\n', encoding='utf-8')
            output = Path(td) / '.env'
            output.write_text('existing', encoding='utf-8')
            with patch.object(cp, 'input_yes_no', return_value=False):
                rc = cp.main(['--template', str(template), '--output', str(output)])
        self.assertEqual(rc, 1)



if __name__ == '__main__':
    unittest.main()
