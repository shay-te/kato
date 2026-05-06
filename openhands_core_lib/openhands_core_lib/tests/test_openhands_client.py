from __future__ import annotations

import os
import types
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import ANY, Mock, call, patch

from openhands_core_lib.openhands_core_lib.openhands_client import OpenHandsClient
from openhands_core_lib.openhands_core_lib.data.fields import ImplementationFields
from openhands_core_lib.openhands_core_lib.config_utils import (
    resolved_openhands_base_url,
    resolved_openhands_llm_settings,
)
from openhands_core_lib.openhands_core_lib.helpers.result_utils import (
    build_openhands_result,
    openhands_session_id,
    openhands_success_flag,
)
from openhands_core_lib.openhands_core_lib.helpers.text_utils import (
    condensed_text,
    normalized_lower_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)
from openhands_core_lib.openhands_core_lib.helpers import agent_prompt_utils
from openhands_core_lib.openhands_core_lib.helpers.agent_prompt_utils import (
    IGNORED_REPOSITORY_FOLDERS_ENV,
    agents_instructions_text,
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    repository_scope_text,
    review_comment_context_text,
    review_comment_location_text,
    review_comments_batch_text,
    review_conversation_title,
    review_repository_context,
    security_guardrails_text,
    task_branch_name,
    task_conversation_title,
    workspace_scope_block,
)
from openhands_core_lib.openhands_core_lib.helpers.agents_instruction_utils import (
    agents_instructions_for_path,
    repository_agents_instructions_text,
)
from provider_client_base.provider_client_base.data.fields import ReviewCommentFields
from tests.utils import (
    ClientTimeout,
    assert_client_headers_and_timeout,
    build_review_comment,
    build_task,
    fix_review_comment_with_defaults,
    implement_task_with_defaults,
    mock_response,
    test_task_with_defaults,
)


@dataclass
class PreparedTaskContext:
    branch_name: str = ''
    repositories: list[Any] = field(default_factory=list)
    repository_branches: dict[str, str] = field(default_factory=dict)
    agents_instructions: str = ''


# ---------------------------------------------------------------------------
# OpenHandsClient — init
# ---------------------------------------------------------------------------

class OpenHandsClientInitTests(unittest.TestCase):
    def test_uses_configured_retry_count(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_uses_minimum_retry_count_of_one(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_retries=0)
        self.assertEqual(client.max_retries, 1)

    def test_uses_minimum_poll_settings(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            poll_interval_seconds=0,
            max_poll_attempts=0,
        )
        self.assertEqual(client._poll_interval_seconds, 0.1)
        self.assertEqual(client._max_poll_attempts, 1)

    def test_default_model_smoke_test_disabled(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertFalse(client._model_smoke_test_enabled)

    def test_model_smoke_test_enabled_flag(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token', model_smoke_test_enabled=True,
        )
        self.assertTrue(client._model_smoke_test_enabled)

    def test_default_openrouter_validator_is_none(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertIsNone(client._openrouter_validator)

    def test_custom_openrouter_validator_is_stored(self) -> None:
        validator = Mock()
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token', openrouter_validator=validator,
        )
        self.assertIs(client._openrouter_validator, validator)

    def test_accepts_llm_settings_as_positional_argument(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            3,
            {
                'llm_model': 'openai/gpt-4o',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )
        self.assertEqual(
            client._settings_update_payload(),
            {
                'llm_model': 'openai/gpt-4o',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )

    def test_timeout_is_300(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        assert_client_headers_and_timeout(self, client, 'oh-token', 300)


# ---------------------------------------------------------------------------
# OpenHandsClient — validate_connection
# ---------------------------------------------------------------------------

class OpenHandsClientValidateConnectionTests(unittest.TestCase):
    def test_checks_openhands_v1_api(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        response = mock_response(json_data=1)

        with patch.object(client, '_get', return_value=response) as mock_get, patch.object(
            client, '_post',
        ) as mock_post:
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/api/v1/app-conversations/count')
        mock_post.assert_not_called()

    def test_syncs_llm_settings_to_openhands(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'bedrock/qwen.qwen3-coder-480b'},
        )
        count_response = mock_response(json_data=1)
        settings_response = mock_response()

        with patch.object(client, '_get', return_value=count_response), patch.object(
            client, '_post', return_value=settings_response,
        ) as mock_post:
            client.validate_connection()

        mock_post.assert_called_once_with(
            '/api/settings',
            json={'llm_model': 'bedrock/qwen.qwen3-coder-480b'},
        )
        settings_response.raise_for_status.assert_called_once_with()

    def test_skips_settings_sync_without_llm_model(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_base_url': 'https://api.openai.com/v1'},
        )
        with patch.object(client, '_get', return_value=mock_response(json_data=1)), \
                patch.object(client, '_post') as mock_post:
            client.validate_connection()

        mock_post.assert_not_called()

    def test_syncs_base_url_when_provided(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={
                'llm_model': 'openai/gpt-4o',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )
        with patch.object(client, '_get', return_value=mock_response(json_data=1)), \
                patch.object(client, '_post', return_value=mock_response()) as mock_post:
            client.validate_connection()

        mock_post.assert_called_once_with(
            '/api/settings',
            json={
                'llm_model': 'openai/gpt-4o',
                'llm_base_url': 'https://api.openai.com/v1',
            },
        )

    def test_runs_model_smoke_test_when_enabled(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openai/gpt-4o'},
            model_smoke_test_enabled=True,
        )
        with patch.object(client, '_get', return_value=mock_response(json_data=1)), \
                patch.object(client, '_post', return_value=mock_response()), \
                patch.object(
                    client, '_run_prompt_result',
                    return_value={'success': True, 'summary': 'hi'},
                ) as mock_run:
            client.validate_connection()

        mock_run.assert_called_once()
        self.assertIn('Reply with exactly hi', mock_run.call_args.kwargs['prompt'])
        self.assertEqual(mock_run.call_args.kwargs['title'], 'Model validation')

    def test_smoke_test_runs_only_once_per_client(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openai/gpt-4o'},
            model_smoke_test_enabled=True,
        )
        with patch.object(client, '_get', return_value=mock_response(json_data=1)), \
                patch.object(client, '_post', return_value=mock_response()), \
                patch.object(
                    client, '_run_prompt_result',
                    return_value={'success': True, 'summary': 'hi'},
                ) as mock_run:
            client.validate_connection()
            client.validate_model_access()

        self.assertEqual(mock_run.call_count, 1)


# ---------------------------------------------------------------------------
# OpenHandsClient — validate_model_access
# ---------------------------------------------------------------------------

class OpenHandsClientValidateModelAccessTests(unittest.TestCase):
    def test_runs_smoke_test_even_when_startup_smoke_test_is_disabled(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openai/gpt-4o'},
            model_smoke_test_enabled=False,
        )
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': True, 'summary': 'hi'},
        ) as mock_run:
            client.validate_model_access()

        mock_run.assert_called_once()
        self.assertIn('Reply with exactly hi', mock_run.call_args.kwargs['prompt'])

    def test_skips_all_checks_when_no_llm_model(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        with patch.object(client, '_run_prompt_result') as mock_run:
            client.validate_model_access()
        mock_run.assert_not_called()

    def test_calls_openrouter_validator_for_openrouter_model(self) -> None:
        validator = Mock()
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={
                'llm_model': 'openrouter/openai/gpt-4o-mini',
                'llm_base_url': 'https://openrouter.ai/api/v1',
            },
            openrouter_validator=validator,
        )
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': True, 'summary': 'hi'},
        ), patch.dict(os.environ, {'LLM_API_KEY': 'or-key'}, clear=False):
            client.validate_model_access()

        validator.assert_called_once_with(
            'openrouter/openai/gpt-4o-mini',
            'https://openrouter.ai/api/v1',
            'or-key',
            client.max_retries,
        )

    def test_openrouter_validator_raises_when_no_api_key(self) -> None:
        validator = Mock()
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openrouter/openai/gpt-4o-mini'},
            openrouter_validator=validator,
        )
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': True, 'summary': 'hi'},
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'OpenRouter model validation requires LLM_API_KEY'):
                client.validate_model_access()

        validator.assert_not_called()

    def test_skips_openrouter_validation_when_no_validator_injected(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openrouter/openai/gpt-4o-mini'},
        )
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': True, 'summary': 'hi'},
        ) as mock_run:
            client.validate_model_access()

        mock_run.assert_called_once()

    def test_smoke_test_failure_raises_runtime_error(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openai/gpt-4o'},
        )
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': False, 'summary': 'no response'},
        ):
            with self.assertRaisesRegex(RuntimeError, 'Model validation returned a failure result'):
                client.validate_model_access()

    def test_uses_openhands_llm_api_key_env_var_as_fallback(self) -> None:
        validator = Mock()
        client = OpenHandsClient(
            'https://openhands.example',
            'oh-token',
            llm_settings={'llm_model': 'openrouter/model'},
            openrouter_validator=validator,
        )
        env = {'OPENHANDS_LLM_API_KEY': 'fallback-key'}
        with patch.object(
            client, '_run_prompt_result',
            return_value={'success': True, 'summary': 'hi'},
        ), patch.dict(os.environ, env, clear=True):
            client.validate_model_access()

        validator.assert_called_once()
        self.assertEqual(validator.call_args[0][2], 'fallback-key')


# ---------------------------------------------------------------------------
# OpenHandsClient — implement_task
# ---------------------------------------------------------------------------

class OpenHandsClientImplementTaskTests(unittest.TestCase):
    def test_uses_v1_conversation_flow(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        task = build_task()

        with patch.object(client, 'logger', client.logger), patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client, '_patch', return_value=mock_response(),
        ) as mock_patch, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{
                    'id': 'conversation-1', 'execution_status': 'finished',
                }]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': (
                            '{"success": true, "summary": "Implemented task", '
                            '"commit_message": "Implement PROJ-1"}'
                        )}],
                    },
                }]}),
            ],
        ) as mock_get:
            result = client.implement_task(task)

        self.assertEqual(result, {
            'branch_name': 'feature/proj-1',
            'summary': 'Implemented task',
            ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-1',
        })
        self.assertEqual(mock_post.call_args.args, ('/api/v1/app-conversations',))
        request_body = mock_post.call_args.kwargs['json']
        self.assertEqual(request_body['title'], 'PROJ-1')
        self.assertIn('Implement task PROJ-1: Fix bug', request_body['initial_message']['content'][0]['text'])
        self.assertIn('Files changed:', request_body['initial_message']['content'][0]['text'])
        self.assertEqual(
            [c.args[0] for c in mock_get.call_args_list],
            [
                '/api/v1/app-conversations/start-tasks',
                '/api/v1/app-conversations',
                '/api/v1/conversation/conversation-1/events/search',
            ],
        )
        mock_patch.assert_called_once_with(
            '/api/conversations/conversation-1',
            headers={'X-Session-API-Key': 'oh-token'},
            json={'title': 'PROJ-1'},
        )
        client.logger.info.assert_any_call('requesting implementation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'implementation finished for task %s with success=%s', 'PROJ-1', True,
        )

    def test_starts_fresh_conversation_even_with_uuid_session(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        valid_session_id = '570ac918-7d72-42b1-b8fa-c4d06ca6f5f0'

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = implement_task_with_defaults(client, session_id=valid_session_id)

        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-1')
        self.assertNotIn('parent_conversation_id', mock_post.call_args.kwargs['json'])

    def test_ignores_non_uuid_session_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            implement_task_with_defaults(client, session_id='conversation-1')

        self.assertNotIn('parent_conversation_id', mock_post.call_args.kwargs['json'])

    def test_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            side_effect=[
                ClientTimeout('gateway timeout'),
                mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
            ],
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_does_not_retry_non_transient_exception(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(client, '_post', side_effect=ValueError('invalid request')) as mock_post:
            with self.assertRaisesRegex(ValueError, 'invalid request'):
                implement_task_with_defaults(client)

        self.assertEqual(mock_post.call_count, 1)


# ---------------------------------------------------------------------------
# OpenHandsClient — test_task
# ---------------------------------------------------------------------------

class OpenHandsClientTestTaskTests(unittest.TestCase):
    def test_posts_testing_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()

        with patch.object(client, 'logger', client.logger), patch.object(
            client, '_run_prompt',
            return_value={
                'summary': 'Added tests and validated the implementation',
                ImplementationFields.COMMIT_MESSAGE: 'Finalize PROJ-1',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-2',
            },
        ) as mock_run_prompt:
            result = test_task_with_defaults(client)

        self.assertEqual(result, {
            'summary': 'Added tests and validated the implementation',
            ImplementationFields.COMMIT_MESSAGE: 'Finalize PROJ-1',
            ImplementationFields.SUCCESS: True,
            ImplementationFields.SESSION_ID: 'conversation-2',
        })
        self.assertIn('Act as a separate testing agent.', mock_run_prompt.call_args.kwargs['prompt'])
        self.assertIn('Do not create a pull request.', mock_run_prompt.call_args.kwargs['prompt'])
        client.logger.info.assert_any_call('requesting testing validation for task %s', 'PROJ-1')
        client.logger.info.assert_any_call(
            'testing validation finished for task %s with success=%s', 'PROJ-1', True,
        )

    def test_retries_on_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            side_effect=[
                ClientTimeout('gateway timeout'),
                mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
            ],
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = test_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)


# ---------------------------------------------------------------------------
# OpenHandsClient — fix_review_comment
# ---------------------------------------------------------------------------

class OpenHandsClientFixReviewCommentTests(unittest.TestCase):
    def test_posts_prompt(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        comment = build_review_comment()

        with patch.object(client, 'logger', client.logger), patch.object(
            client, '_run_prompt',
            return_value={
                'summary': 'Updated branch',
                ImplementationFields.COMMIT_MESSAGE: 'Address review comments',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-3',
            },
        ) as mock_run_prompt:
            result = fix_review_comment_with_defaults(client, comment)

        self.assertEqual(result['branch_name'], 'feature/proj-1')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-3')
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertIn(
            'Comment by reviewer: Please rename this variable.',
            mock_run_prompt.call_args.kwargs['prompt'],
        )
        self.assertIn('always include its required command field', mock_run_prompt.call_args.kwargs['prompt'])
        self.assertIn(
            'Do not report success until all intended changes are saved',
            mock_run_prompt.call_args.kwargs['prompt'],
        )
        self.assertEqual(mock_run_prompt.call_args.kwargs['title'], 'Fix review comment 99')
        client.logger.info.assert_any_call(
            'review fix finished for pull request %s with %d comment(s) success=%s',
            '17', 1, True,
        )

    def test_uses_task_based_conversation_title_when_available(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_run_prompt',
            return_value={
                'summary': 'Updated branch',
                ImplementationFields.COMMIT_MESSAGE: 'Address review comments',
                ImplementationFields.SUCCESS: True,
                ImplementationFields.SESSION_ID: 'conversation-3',
            },
        ) as mock_run_prompt:
            fix_review_comment_with_defaults(client, task_id='PROJ-1', task_summary='Fix bug')

        self.assertEqual(mock_run_prompt.call_args.kwargs['title'], 'PROJ-1 [review]')

    def test_uses_parent_conversation_id_for_uuid_session(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        valid_session_id = '570ac918-7d72-42b1-b8fa-c4d06ca6f5f0'

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = fix_review_comment_with_defaults(client, session_id=valid_session_id)

        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-1')
        self.assertEqual(
            mock_post.call_args.kwargs['json']['parent_conversation_id'],
            '570ac9187d7242b1b8fac4d06ca6f5f0',
        )

    def test_prompt_includes_prior_comment_context(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comment = build_review_comment()
        setattr(
            comment,
            ReviewCommentFields.ALL_COMMENTS,
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                },
                {
                    ReviewCommentFields.COMMENT_ID: '99',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please rename this variable.',
                },
            ],
        )

        prompt = client._build_review_prompt(comment, 'feature/proj-1')

        self.assertIn('Review comment context:', prompt)
        self.assertIn('- reviewer: Please add a test.', prompt)
        self.assertIn('When you finish, use the finish tool.', prompt)
        self.assertIn('Make the smallest possible change needed to address the review comment.', prompt)

    def test_retries_on_transient_response(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        retry_response = mock_response(status_code=503)
        success_response = mock_response(json_data={'id': 'start-1', 'status': 'WORKING'})

        with patch.object(
            client, '_post',
            side_effect=[retry_response, success_response],
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = fix_review_comment_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(mock_post.call_count, 2)

    def test_raises_when_no_comments(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        with self.assertRaisesRegex(ValueError, 'requires at least one comment'):
            client.fix_review_comments([], 'feature/branch')

    def test_batch_prompt_for_multiple_comments(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comments = [
            build_review_comment(body='Fix typo.'),
            build_review_comment(body='Add docstring.'),
        ]
        with patch.object(
            client, '_run_prompt',
            return_value={'success': True, 'summary': 'done', ImplementationFields.SESSION_ID: 'c-1'},
        ) as mock_run:
            with patch.object(client, '_patch', return_value=mock_response()):
                client.fix_review_comments(comments, 'branch')

        prompt = mock_run.call_args.kwargs['prompt']
        self.assertIn('Fix typo.', prompt)
        self.assertIn('Add docstring.', prompt)

    def test_answer_mode_single_comment(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comment = build_review_comment(body='What does this method do?')

        prompt = client._build_review_prompt(comment, 'feature/main', mode='answer')

        self.assertIn('These are QUESTIONS, not fix requests.', prompt)
        self.assertIn('Read the relevant code; do NOT modify any files.', prompt)
        self.assertIn('What does this method do?', prompt)

    def test_answer_mode_batch_comments(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comments = [build_review_comment(body='Why?'), build_review_comment(body='How?')]

        prompt = client._build_review_comments_batch_prompt(comments, 'branch', mode='answer')

        self.assertIn('These are QUESTIONS, not fix requests.', prompt)
        self.assertIn('Why?', prompt)


# ---------------------------------------------------------------------------
# OpenHandsClient — delete / stop
# ---------------------------------------------------------------------------

class OpenHandsClientStopTests(unittest.TestCase):
    def test_delete_conversation_calls_delete_endpoint(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        delete_mock = Mock(return_value=mock_response())

        with patch.object(client, '_delete', delete_mock):
            client.delete_conversation('conv-1')

        delete_mock.assert_called_once_with('/api/conversations/conv-1')

    def test_delete_conversation_logs_warning_on_failure(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()

        with patch.object(client, '_delete', side_effect=RuntimeError('network error')):
            client.delete_conversation('conv-1')

        client.logger.warning.assert_called_once()

    def test_stop_all_conversations_deletes_every_listed_conversation(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token',
            poll_interval_seconds=0, max_poll_attempts=3,
        )
        client.logger = Mock()
        delete_mock = Mock(return_value=mock_response())

        with patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[
                    {'id': 'conv-1', 'execution_status': 'running'},
                    {'id': 'conv-2', 'execution_status': 'paused'},
                ]),
                mock_response(json_data=[
                    {'id': 'conv-1', 'execution_status': 'running'},
                    {'id': 'conv-2', 'execution_status': 'paused'},
                ]),
                mock_response(json_data=[]),
            ],
        ), patch.object(client, '_delete', delete_mock), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.time.sleep',
        ) as sleep_mock:
            client.stop_all_conversations()

        self.assertEqual(delete_mock.call_count, 2)
        delete_mock.assert_any_call('/api/conversations/conv-1')
        delete_mock.assert_any_call('/api/conversations/conv-2')
        sleep_mock.assert_called_once_with(0.1)
        client.logger.info.assert_any_call(
            'waiting for %s OpenHands conversations to stop during shutdown', 2,
        )

    def test_stop_all_conversations_waits_until_list_empty(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token',
            poll_interval_seconds=0, max_poll_attempts=3,
        )
        client.logger = Mock()
        delete_mock = Mock(return_value=mock_response())

        with patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{'id': 'conv-1', 'execution_status': 'running'}]),
                mock_response(json_data=[{'id': 'conv-1', 'execution_status': 'running'}]),
                mock_response(json_data=[]),
            ],
        ), patch.object(client, '_delete', delete_mock), patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.time.sleep',
        ):
            client.stop_all_conversations()

        delete_mock.assert_called_once_with('/api/conversations/conv-1')

    def test_stop_all_conversations_continues_on_list_failure(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()
        get_mock = Mock(side_effect=RuntimeError('network error'))

        with patch.object(client, '_get', get_mock):
            client.stop_all_conversations()

        get_mock.assert_called_once_with('/api/v1/app-conversations')
        client.logger.warning.assert_any_call(
            'failed to list conversations for shutdown cleanup; '
            'skipping remaining container removal: %s',
            ANY,
        )


# ---------------------------------------------------------------------------
# OpenHandsClient — conversation polling and error paths
# ---------------------------------------------------------------------------

class OpenHandsClientConversationPollingTests(unittest.TestCase):
    def test_wait_for_conversation_result_uses_poll_limit_in_timeout(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_poll_attempts=2)

        with patch.object(
            client, '_get_conversation',
            return_value={'id': 'conversation-1', 'execution_status': 'running'},
        ), patch.object(
            client, '_log_conversation_highlights', return_value=True,
        ), patch('openhands_core_lib.openhands_core_lib.openhands_client.time.sleep'):
            with self.assertRaisesRegex(
                TimeoutError,
                'openhands conversation conversation-1 did not finish after 2 polls',
            ):
                client._wait_for_conversation_result('conversation-1')

    def test_wait_for_conversation_result_logs_highlights_while_active(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()

        with patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'working'}]),
                mock_response(json_data={'items': [{
                    'id': 'evt-1', 'kind': 'ActionEvent', 'source': 'agent',
                    'tool_name': 'execute_bash',
                    'tool_call': {'arguments': '{"command":"git status"}'},
                }]}),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'working'}]),
                mock_response(json_data={'items': [{
                    'id': 'evt-1', 'kind': 'ActionEvent', 'source': 'agent',
                    'tool_name': 'execute_bash',
                    'tool_call': {'arguments': '{"command":"git status"}'},
                }]}),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
                    'tool_call': {'arguments': '{"summary":"ok"}'},
                }]}),
            ],
        ), patch.object(client, '_sleep_before_next_poll') as mock_sleep:
            result = client._wait_for_conversation_result('conversation-1', 'UNA-1')

        self.assertEqual(result, {'success': True, 'summary': 'ok'})
        client.logger.info.assert_called_once_with(
            'Mission %s: Agent %s', 'UNA-1', 'ran shell command: git status',
        )
        self.assertEqual(mock_sleep.call_count, 2)

    def test_raises_on_failed_execution_status(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'failed'}]),
                mock_response(json_data={'items': [{
                    'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'bash',
                    'tool_call': {'arguments': '{"command":"git pull"}'},
                }]}),
            ],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'openhands conversation failed with status: failed: recent agent activity: ran shell command: git pull',
            ):
                implement_task_with_defaults(client)

    def test_raises_when_start_task_errors(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            return_value=mock_response(json_data=[{
                'id': 'start-1', 'status': 'ERROR', 'detail': 'sandbox failed to boot',
            }]),
        ):
            with self.assertRaisesRegex(RuntimeError, 'sandbox failed to boot'):
                implement_task_with_defaults(client)

    def test_retries_retryable_start_task_error_and_succeeds(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token', max_retries=2)

        with patch.object(
            client, '_post',
            side_effect=[
                mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
                mock_response(json_data={'id': 'start-2', 'status': 'WORKING'}),
            ],
        ) as mock_post, patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'ERROR',
                    'detail': '500: Sandbox entered error state: oh-agent-server-1',
                }]),
                mock_response(json_data=[{
                    'id': 'start-2', 'status': 'READY',
                    'app_conversation_id': 'conversation-2',
                }]),
                mock_response(json_data=[{'id': 'conversation-2', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': '{"success": true, "summary": "ok"}'}],
                    },
                }]}),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conversation-2')
        self.assertEqual(mock_post.call_count, 2)

    def test_raises_when_start_task_ready_without_conversation_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            return_value=mock_response(json_data=[{'id': 'start-1', 'status': 'READY'}]),
        ):
            with self.assertRaisesRegex(ValueError, 'without a conversation id'):
                implement_task_with_defaults(client)

    def test_raises_when_start_task_response_has_no_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(client, '_post', return_value=mock_response(json_data={})):
            with self.assertRaisesRegex(ValueError, 'openhands start task response did not include an id'):
                implement_task_with_defaults(client)

    def test_raises_timeout_when_conversation_never_finishes(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token', max_poll_attempts=1,
        )
        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'working'}]),
                mock_response(json_data={'items': []}),
            ],
        ), patch.object(client, '_sleep_before_next_poll'):
            with self.assertRaisesRegex(
                TimeoutError,
                'openhands conversation conversation-1 did not finish after 1 polls',
            ):
                implement_task_with_defaults(client)

    def test_raises_timeout_when_start_task_never_readies(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token', max_poll_attempts=1,
        )
        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            return_value=mock_response(json_data=[{'id': 'start-1', 'status': 'WORKING'}]),
        ), patch.object(client, '_sleep_before_next_poll'):
            with self.assertRaisesRegex(
                TimeoutError,
                'openhands did not start a conversation after 1 polls',
            ):
                implement_task_with_defaults(client)


# ---------------------------------------------------------------------------
# OpenHandsClient — event parsing
# ---------------------------------------------------------------------------

class OpenHandsClientEventParsingTests(unittest.TestCase):
    def test_highlight_text_describes_shell_action(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'ActionEvent', 'source': 'agent',
            'tool_name': 'execute_bash',
            'tool_call': {'arguments': '{"command":"git status"}'},
        })
        self.assertEqual(highlight, 'ran shell command: git status')

    def test_highlight_text_describes_file_editor_str_replace(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'ActionEvent', 'source': 'agent',
            'tool_name': 'file_editor',
            'tool_call': {'arguments': '{"command":"str_replace","path":"/workspace/app.js"}'},
        })
        self.assertEqual(highlight, 'edited /workspace/app.js with str_replace')

    def test_highlight_text_describes_file_editor_view(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'ActionEvent', 'source': 'agent',
            'tool_name': 'file_editor',
            'tool_call': {'arguments': '{"command":"view","path":"/workspace/main.py"}'},
        })
        self.assertEqual(highlight, 'viewed /workspace/main.py')

    def test_highlight_text_falls_back_to_running_line(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'MessageEvent', 'source': 'agent',
            'llm_message': {
                'role': 'assistant',
                'content': [{'text': 'Let me inspect first.\nRunning git diff --stat'}],
            },
        })
        self.assertEqual(highlight, 'Running git diff --stat')

    def test_highlight_returns_empty_for_finish_action(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
            'tool_call': {'arguments': '{"summary":"done"}'},
        })
        self.assertEqual(highlight, '')

    def test_highlight_returns_empty_for_non_agent_source(self) -> None:
        highlight = OpenHandsClient._event_highlight_text({
            'kind': 'ActionEvent', 'source': 'user', 'tool_name': 'execute_bash',
            'tool_call': {'arguments': '{"command":"ls"}'},
        })
        self.assertEqual(highlight, '')

    def test_parse_result_json_reads_plain_json(self) -> None:
        payload = OpenHandsClient._parse_result_json('{"success": true, "summary": "ok"}')
        self.assertEqual(payload, {'success': True, 'summary': 'ok'})

    def test_parse_result_json_reads_fenced_json(self) -> None:
        payload = OpenHandsClient._parse_result_json(
            '```json\n{"success": true, "summary": "ok"}\n```'
        )
        self.assertEqual(payload, {'success': True, 'summary': 'ok'})

    def test_parse_result_json_extracts_from_prose(self) -> None:
        payload = OpenHandsClient._parse_result_json(
            'Here is the result:\n{"success": true, "summary": "done"}\nEnd.'
        )
        self.assertEqual(payload, {'success': True, 'summary': 'done'})

    def test_parse_result_json_returns_none_for_non_json(self) -> None:
        payload = OpenHandsClient._parse_result_json('not json')
        self.assertIsNone(payload)

    def test_parse_result_json_returns_none_for_empty(self) -> None:
        payload = OpenHandsClient._parse_result_json('')
        self.assertIsNone(payload)

    def test_tool_call_arguments_logs_invalid_json(self) -> None:
        with patch('openhands_core_lib.openhands_core_lib.openhands_client.logger') as mock_logger:
            payload = OpenHandsClient._tool_call_arguments(
                {'tool_call': {'arguments': '{not valid json'}}
            )
        self.assertEqual(payload, {})
        mock_logger.warning.assert_called_once()

    def test_finish_action_payload_reads_summary_from_tool_arguments(self) -> None:
        event = {
            'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
            'tool_call': {
                'arguments': '{"summary":"Files changed:\\n- app.js","message":"Done."}'
            },
        }
        payload = OpenHandsClient._finish_action_payload(event)
        self.assertIsNotNone(payload)
        self.assertTrue(payload[ImplementationFields.SUCCESS])
        self.assertEqual(payload['summary'], 'Files changed:\n- app.js')

    def test_finish_action_payload_falls_back_to_action_summary(self) -> None:
        event = {
            'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
            'action': {'summary': 'fallback summary', 'message': 'msg'},
        }
        payload = OpenHandsClient._finish_action_payload(event)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['summary'], 'fallback summary')

    def test_finish_action_payload_returns_none_for_non_finish_event(self) -> None:
        event = {'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'execute_bash'}
        self.assertIsNone(OpenHandsClient._finish_action_payload(event))

    def test_assistant_message_text_extracts_text(self) -> None:
        event = {
            'kind': 'MessageEvent', 'source': 'agent',
            'llm_message': {
                'role': 'assistant',
                'content': [{'text': 'part1'}, {'text': 'part2'}],
            },
        }
        text = OpenHandsClient._assistant_message_text(event)
        self.assertEqual(text, 'part1\npart2')

    def test_assistant_message_text_returns_empty_for_non_assistant(self) -> None:
        event = {
            'kind': 'MessageEvent', 'source': 'agent',
            'llm_message': {'role': 'user', 'content': [{'text': 'user msg'}]},
        }
        self.assertEqual(OpenHandsClient._assistant_message_text(event), '')

    def test_log_conversation_highlights_deduplicates_same_display_text(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        client.logger = Mock()

        with patch.object(
            client, '_get_conversation_events',
            return_value=[
                {
                    'id': 'evt-3', 'kind': 'ActionEvent', 'source': 'agent',
                    'tool_name': 'file_editor',
                    'tool_call': {'arguments': '{"command":"view","path":"/workspace/app.js"}'},
                },
                {
                    'id': 'evt-2', 'kind': 'ActionEvent', 'source': 'agent',
                    'tool_name': 'terminal',
                    'tool_call': {'arguments': '{}'},
                },
                {
                    'id': 'evt-1', 'kind': 'ActionEvent', 'source': 'agent',
                    'tool_name': 'terminal',
                    'tool_call': {'arguments': '{}'},
                },
            ],
        ):
            result = client._log_conversation_highlights('conversation-1', 'UNA-1 [review]', set())

        self.assertTrue(result)
        self.assertEqual(
            [c.args for c in client.logger.info.call_args_list],
            [
                ('Mission %s: Agent %s', 'UNA-1 [review]', 'used terminal'),
                ('Mission %s: Agent %s', 'UNA-1 [review]', 'viewed /workspace/app.js'),
            ],
        )

    def test_normalized_uuid_parses_standard_uuid(self) -> None:
        result = OpenHandsClient._normalized_uuid('570ac918-7d72-42b1-b8fa-c4d06ca6f5f0')
        self.assertEqual(result, '570ac9187d7242b1b8fac4d06ca6f5f0')

    def test_normalized_uuid_returns_empty_for_non_uuid(self) -> None:
        self.assertEqual(OpenHandsClient._normalized_uuid('not-a-uuid'), '')
        self.assertEqual(OpenHandsClient._normalized_uuid(''), '')
        self.assertEqual(OpenHandsClient._normalized_uuid(None), '')

    def test_result_fallback_to_title_when_no_parseable_events(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'MessageEvent', 'source': 'agent',
                    'llm_message': {
                        'role': 'assistant',
                        'content': [{'text': 'not json'}],
                    },
                }]}),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'PROJ-1')

    def test_result_from_finish_action_event(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [{
                    'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
                    'action': {
                        'summary': 'Files changed:\n- src/app.ts\n  Hardened retries.',
                        'message': 'Done.',
                    },
                }]}),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Files changed:\n- src/app.ts\n  Hardened retries.')

    def test_result_finds_parseable_result_even_when_newest_event_is_not_json(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')

        with patch.object(
            client, '_post',
            return_value=mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ), patch.object(
            client, '_get',
            side_effect=[
                mock_response(json_data=[{
                    'id': 'start-1', 'status': 'READY',
                    'app_conversation_id': 'conversation-1',
                }]),
                mock_response(json_data=[{'id': 'conversation-1', 'execution_status': 'finished'}]),
                mock_response(json_data={'items': [
                    {
                        'kind': 'MessageEvent', 'source': 'agent',
                        'llm_message': {
                            'role': 'assistant',
                            'content': [{'text': 'still working'}],
                        },
                    },
                    {
                        'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
                        'tool_call': {'arguments': '{"success": true, "summary": "Fixed."}'},
                    },
                ]}),
            ],
        ):
            result = implement_task_with_defaults(client)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Fixed.')


# ---------------------------------------------------------------------------
# OpenHandsClient — prompt builders
# ---------------------------------------------------------------------------

class OpenHandsClientPromptBuilderTests(unittest.TestCase):
    def test_implementation_prompt_does_not_embed_testing_commands(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        prompt = client._build_implementation_prompt(build_task())

        self.assertNotIn('Act as a separate testing agent.', prompt)
        self.assertIn('When you finish, use the finish tool.', prompt)
        self.assertIn('Make the smallest possible change needed to satisfy the task.', prompt)
        self.assertIn('Do not report success until all intended changes are saved', prompt)
        self.assertIn('Do not run npm run build', prompt)
        self.assertIn('Create validation_report.md in the repository root when the task succeeds.', prompt)
        self.assertIn('Security guardrails:', prompt)
        self.assertIn('Never use create_pr', prompt)
        self.assertIn('Files changed:', prompt)

    def test_implementation_prompt_includes_workspace_scope_when_prepared(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        repository = types.SimpleNamespace(id='client', local_path='/workspace/project', destination_branch='main')
        prepared_task = PreparedTaskContext(
            branch_name='UNA-222',
            repositories=[repository],
            repository_branches={'client': 'UNA-222'},
        )
        prompt = client._build_implementation_prompt(build_task(), prepared_task)
        self.assertIn('WORKSPACE SCOPE', prompt)

    def test_implementation_prompt_includes_agents_instructions(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        repository = types.SimpleNamespace(id='client', local_path='/workspace/project', destination_branch='main')
        prepared_task = PreparedTaskContext(
            branch_name='UNA-222',
            repositories=[repository],
            agents_instructions='Repository AGENTS.md instructions:\nAGENTS.md:\nUse pnpm.',
        )
        prompt = client._build_implementation_prompt(build_task(), prepared_task)
        self.assertIn('Repository AGENTS.md instructions:', prompt)
        self.assertIn('Use pnpm.', prompt)
        self.assertLess(
            prompt.index('Repository AGENTS.md instructions:'),
            prompt.index('Security guardrails:'),
        )

    def test_repository_scope_instructions_with_prepared_repositories(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        repository = types.SimpleNamespace(id='client', local_path='/workspace/project', destination_branch='main')
        task = build_task(
            task_id='UNA-222', branch_name='UNA-222',
            repositories=[repository],
            repository_branches={'client': 'UNA-222'},
        )
        prompt = client._build_implementation_prompt(task)
        self.assertIn('Only modify these repositories:', prompt)
        self.assertIn('the orchestration layer already prepared branch UNA-222 from main', prompt)
        self.assertIn('Stay on the current branch and do not run git checkout', prompt)

    def test_testing_prompt_describes_separate_agent(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        prompt = client._build_testing_prompt(build_task())

        self.assertIn('Act as a separate testing agent.', prompt)
        self.assertIn('Write additional tests when needed', prompt)
        self.assertIn('Do not create a pull request.', prompt)
        self.assertIn('Security guardrails:', prompt)
        self.assertIn('Create validation_report.md', prompt)
        self.assertIn('run the relevant tests', prompt)

    def test_task_conversation_title_uses_task_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertEqual(
            client._task_conversation_title(build_task(task_id='UNA-2405', summary='do xyz')),
            'UNA-2405',
        )

    def test_task_conversation_title_with_suffix(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertEqual(
            client._task_conversation_title(
                build_task(task_id='UNA-2405', summary='do xyz'), suffix=' [testing]',
            ),
            'UNA-2405 [testing]',
        )

    def test_task_conversation_title_normalizes_blank_id_uses_summary(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertEqual(
            client._task_conversation_title(build_task(task_id='', summary='do xyz')),
            'do xyz',
        )

    def test_task_conversation_title_fallback_when_both_blank(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        self.assertEqual(
            client._task_conversation_title(build_task(task_id='', summary='   ')),
            'Task',
        )

    def test_review_conversation_title_uses_task_id_when_present(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        title = client._review_conversation_title(build_review_comment(), task_id='UNA-5', task_summary='Fix')
        self.assertEqual(title, 'UNA-5 [review]')

    def test_review_conversation_title_falls_back_to_comment_id(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        title = client._review_conversation_title(build_review_comment())
        self.assertEqual(title, 'Fix review comment 99')


# ---------------------------------------------------------------------------
# Config utils
# ---------------------------------------------------------------------------

class ConfigUtilsTests(unittest.TestCase):
    def test_resolved_base_url_returns_base_url(self) -> None:
        cfg = types.SimpleNamespace(base_url='https://openhands.example', testing_container_enabled=False)
        self.assertEqual(resolved_openhands_base_url(cfg), 'https://openhands.example')

    def test_resolved_base_url_returns_testing_url_when_testing_enabled(self) -> None:
        cfg = types.SimpleNamespace(
            base_url='https://openhands.example',
            testing_container_enabled=True,
            testing_base_url='https://test.openhands.example',
        )
        self.assertEqual(
            resolved_openhands_base_url(cfg, testing=True),
            'https://test.openhands.example',
        )

    def test_resolved_base_url_ignores_testing_url_when_not_testing(self) -> None:
        cfg = types.SimpleNamespace(
            base_url='https://openhands.example',
            testing_container_enabled=True,
            testing_base_url='https://test.openhands.example',
        )
        self.assertEqual(resolved_openhands_base_url(cfg, testing=False), 'https://openhands.example')

    def test_resolved_base_url_ignores_testing_url_when_flag_disabled(self) -> None:
        cfg = types.SimpleNamespace(
            base_url='https://openhands.example',
            testing_container_enabled=False,
            testing_base_url='https://test.openhands.example',
        )
        self.assertEqual(resolved_openhands_base_url(cfg, testing=True), 'https://openhands.example')

    def test_resolved_llm_settings_returns_prod_settings(self) -> None:
        cfg = types.SimpleNamespace(
            llm_model='openai/gpt-4o',
            llm_base_url='https://api.openai.com/v1',
            testing_container_enabled=False,
        )
        result = resolved_openhands_llm_settings(cfg)
        self.assertEqual(result, {'llm_model': 'openai/gpt-4o', 'llm_base_url': 'https://api.openai.com/v1'})

    def test_resolved_llm_settings_returns_testing_settings_when_enabled(self) -> None:
        cfg = types.SimpleNamespace(
            llm_model='openai/gpt-4o',
            llm_base_url='https://api.openai.com/v1',
            testing_container_enabled=True,
            testing_llm_model='openai/gpt-4o-mini',
            testing_llm_base_url='https://test.api/v1',
        )
        result = resolved_openhands_llm_settings(cfg, testing=True)
        self.assertEqual(result, {'llm_model': 'openai/gpt-4o-mini', 'llm_base_url': 'https://test.api/v1'})

    def test_resolved_llm_settings_returns_prod_settings_when_not_testing(self) -> None:
        cfg = types.SimpleNamespace(
            llm_model='openai/gpt-4o',
            llm_base_url='',
            testing_container_enabled=True,
            testing_llm_model='mini',
            testing_llm_base_url='',
        )
        result = resolved_openhands_llm_settings(cfg, testing=False)
        self.assertEqual(result, {'llm_model': 'openai/gpt-4o', 'llm_base_url': ''})


# ---------------------------------------------------------------------------
# Result utils
# ---------------------------------------------------------------------------

class ResultUtilsTests(unittest.TestCase):
    def test_openhands_success_flag_reads_bool_true(self) -> None:
        self.assertTrue(openhands_success_flag({'success': True}))

    def test_openhands_success_flag_reads_bool_false(self) -> None:
        self.assertFalse(openhands_success_flag({'success': False}))

    def test_openhands_success_flag_reads_string_true(self) -> None:
        self.assertTrue(openhands_success_flag({'success': 'true'}))
        self.assertTrue(openhands_success_flag({'success': 'yes'}))
        self.assertTrue(openhands_success_flag({'success': '1'}))

    def test_openhands_success_flag_reads_string_false(self) -> None:
        self.assertFalse(openhands_success_flag({'success': 'false'}))

    def test_openhands_success_flag_uses_default_when_key_absent(self) -> None:
        self.assertFalse(openhands_success_flag({}))
        self.assertTrue(openhands_success_flag({}, default=True))

    def test_openhands_success_flag_uses_default_for_non_mapping(self) -> None:
        self.assertFalse(openhands_success_flag(None))
        self.assertTrue(openhands_success_flag(None, default=True))

    def test_openhands_session_id_reads_session_id_key(self) -> None:
        self.assertEqual(openhands_session_id({'session_id': 'conv-1'}), 'conv-1')

    def test_openhands_session_id_reads_conversation_id_fallback(self) -> None:
        self.assertEqual(openhands_session_id({'conversation_id': 'conv-2'}), 'conv-2')

    def test_openhands_session_id_returns_empty_when_absent(self) -> None:
        self.assertEqual(openhands_session_id({}), '')
        self.assertEqual(openhands_session_id(None), '')

    def test_build_openhands_result_full(self) -> None:
        result = build_openhands_result(
            {'success': True, 'summary': 'ok', 'commit_message': 'Fix bug', 'message': 'detail'},
            branch_name='feature/xyz',
        )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'ok')
        self.assertEqual(result['branch_name'], 'feature/xyz')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Fix bug')
        self.assertEqual(result[ImplementationFields.MESSAGE], 'detail')

    def test_build_openhands_result_uses_summary_fallback(self) -> None:
        result = build_openhands_result(None, summary_fallback='fallback title', default_success=True)
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'fallback title')

    def test_build_openhands_result_uses_default_commit_message(self) -> None:
        result = build_openhands_result({'success': True}, default_commit_message='Default commit')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Default commit')

    def test_build_openhands_result_payload_commit_overrides_default(self) -> None:
        result = build_openhands_result(
            {'success': True, 'commit_message': 'Specific commit'},
            default_commit_message='Default commit',
        )
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'Specific commit')

    def test_build_openhands_result_omits_branch_when_empty(self) -> None:
        result = build_openhands_result({'success': True, 'summary': 'ok'})
        self.assertNotIn('branch_name', result)


# ---------------------------------------------------------------------------
# Text utils
# ---------------------------------------------------------------------------

class TextUtilsTests(unittest.TestCase):
    def test_normalized_text_strips_whitespace(self) -> None:
        self.assertEqual(normalized_text('  hello  '), 'hello')

    def test_normalized_text_returns_empty_for_none(self) -> None:
        self.assertEqual(normalized_text(None), '')

    def test_normalized_lower_text(self) -> None:
        self.assertEqual(normalized_lower_text('  HELLO  '), 'hello')

    def test_condensed_text_collapses_whitespace(self) -> None:
        self.assertEqual(condensed_text('  hello   world  '), 'hello world')

    def test_text_from_attr_reads_attribute(self) -> None:
        obj = types.SimpleNamespace(name='test')
        self.assertEqual(text_from_attr(obj, 'name'), 'test')

    def test_text_from_attr_returns_empty_for_missing(self) -> None:
        self.assertEqual(text_from_attr(types.SimpleNamespace(), 'missing'), '')

    def test_text_from_mapping_reads_key(self) -> None:
        self.assertEqual(text_from_mapping({'key': 'value'}, 'key'), 'value')

    def test_text_from_mapping_returns_default_for_missing(self) -> None:
        self.assertEqual(text_from_mapping({}, 'missing', 'default'), 'default')

    def test_text_from_mapping_returns_empty_for_non_mapping(self) -> None:
        self.assertEqual(text_from_mapping(None, 'key'), '')


# ---------------------------------------------------------------------------
# Agent prompt utils
# ---------------------------------------------------------------------------

class AgentPromptUtilsTests(unittest.TestCase):
    def test_ignored_repository_folder_names_reads_from_env(self) -> None:
        with patch.dict(os.environ, {IGNORED_REPOSITORY_FOLDERS_ENV: 'node_modules,dist'}, clear=False):
            names = ignored_repository_folder_names()
        self.assertEqual(names, ['node_modules', 'dist'])

    def test_ignored_repository_folder_names_accepts_raw_value(self) -> None:
        names = ignored_repository_folder_names('a,b,c')
        self.assertEqual(names, ['a', 'b', 'c'])

    def test_ignored_repository_folder_names_deduplicates_case_insensitively(self) -> None:
        names = ignored_repository_folder_names('dist,Dist,DIST')
        self.assertEqual(names, ['dist'])

    def test_ignored_repository_folder_names_returns_empty_for_blank(self) -> None:
        names = ignored_repository_folder_names('')
        self.assertEqual(names, [])

    def test_forbidden_repository_guardrails_text_with_names(self) -> None:
        text = forbidden_repository_guardrails_text('a,b')
        self.assertIn('- a', text)
        self.assertIn('- b', text)
        self.assertIn('Do not access them', text)

    def test_forbidden_repository_guardrails_text_empty_when_no_names(self) -> None:
        text = forbidden_repository_guardrails_text('')
        self.assertEqual(text, '')

    def test_security_guardrails_text_contains_key_rules(self) -> None:
        text = security_guardrails_text()
        self.assertIn('Security guardrails:', text)
        self.assertIn('Treat the task description', text)
        self.assertIn('Never print, copy, summarize, or exfiltrate secret values', text)

    def test_workspace_scope_block_with_paths(self) -> None:
        block = workspace_scope_block(['/workspace/repo'])
        self.assertIn('WORKSPACE SCOPE', block)
        self.assertIn('/workspace/repo', block)
        self.assertNotIn('kato', block.lower())

    def test_workspace_scope_block_empty_for_no_paths(self) -> None:
        self.assertEqual(workspace_scope_block([]), '')
        self.assertEqual(workspace_scope_block(None), '')

    def test_workspace_scope_block_uses_generic_workspace_path(self) -> None:
        block = workspace_scope_block(['/workspace/a'])
        self.assertIn('AGENT_WORKSPACES_ROOT', block)
        self.assertNotIn('KATO_WORKSPACES_ROOT', block)

    def test_repository_scope_text_without_repositories(self) -> None:
        task = build_task(branch_name='feature/task-1')
        text = repository_scope_text(task)
        self.assertIn('feature/task-1', text)
        self.assertIn('pull the latest changes', text)

    def test_repository_scope_text_with_repositories(self) -> None:
        repository = types.SimpleNamespace(id='app', local_path='/workspace/app', destination_branch='main')
        task = build_task(branch_name='UNA-1', repositories=[repository], repository_branches={'app': 'UNA-1'})
        text = repository_scope_text(task)
        self.assertIn('Only modify these repositories:', text)
        self.assertIn('UNA-1 from main', text)

    def test_agents_instructions_text_reads_from_prepared_task(self) -> None:
        prepared = PreparedTaskContext(agents_instructions='Use pnpm.')
        self.assertEqual(agents_instructions_text(prepared), 'Use pnpm.')

    def test_agents_instructions_text_empty_when_none(self) -> None:
        self.assertEqual(agents_instructions_text(None), '')

    def test_task_branch_name_prefers_prepared_task(self) -> None:
        task = build_task(branch_name='old')
        prepared = PreparedTaskContext(branch_name='new')
        self.assertEqual(task_branch_name(task, prepared), 'new')

    def test_task_branch_name_falls_back_to_task(self) -> None:
        task = build_task(branch_name='feature/task')
        self.assertEqual(task_branch_name(task), 'feature/task')

    def test_task_conversation_title_uses_id(self) -> None:
        task = build_task(task_id='UNA-1', summary='stuff')
        self.assertEqual(task_conversation_title(task), 'UNA-1')

    def test_task_conversation_title_uses_summary_when_no_id(self) -> None:
        task = build_task(task_id='', summary='fix something long enough to truncate')
        self.assertIn('fix something', task_conversation_title(task))

    def test_task_conversation_title_falls_back_to_task(self) -> None:
        task = build_task(task_id='', summary='')
        self.assertEqual(task_conversation_title(task), 'Task')

    def test_review_conversation_title_with_task_id(self) -> None:
        self.assertEqual(
            review_conversation_title(build_review_comment(), task_id='UNA-5'),
            'UNA-5 [review]',
        )

    def test_review_conversation_title_falls_back_to_comment_id(self) -> None:
        self.assertEqual(
            review_conversation_title(build_review_comment()),
            'Fix review comment 99',
        )

    def test_review_repository_context_with_repo_id(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'repository_id', 'my-repo')
        self.assertEqual(review_repository_context(comment), ' in repository my-repo')

    def test_review_repository_context_empty_without_repo_id(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'repository_id', '')
        self.assertEqual(review_repository_context(comment), '')

    def test_review_comment_context_text_returns_empty_for_single_comment(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'all_comments', [{'author': 'reviewer', 'body': 'fix this'}])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_review_comment_context_text_returns_empty_for_no_comments(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'all_comments', [])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_review_comment_context_text_includes_all_comments(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'all_comments', [
            {'author': 'alice', 'body': 'First comment.'},
            {'author': 'bob', 'body': 'Second comment.'},
        ])
        text = review_comment_context_text(comment)
        self.assertIn('Review comment context:', text)
        self.assertIn('- alice: First comment.', text)
        self.assertIn('- bob: Second comment.', text)

    def test_review_comment_context_text_skips_empty_bodies(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'all_comments', [
            {'author': 'alice', 'body': ''},
            {'author': 'bob', 'body': 'Valid comment.'},
            {'author': 'carol', 'body': 'Another.'},
        ])
        text = review_comment_context_text(comment)
        self.assertIn('bob', text)
        self.assertNotIn('alice', text)

    def test_review_comment_location_text_with_file_and_line(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'file_path', 'src/app.py')
        setattr(comment, 'line_number', 42)
        text = review_comment_location_text(comment)
        self.assertEqual(text, 'File: src/app.py:42')

    def test_review_comment_location_text_empty_without_file(self) -> None:
        comment = build_review_comment()
        setattr(comment, 'file_path', '')
        self.assertEqual(review_comment_location_text(comment), '')

    def test_review_comments_batch_text_formats_all_comments(self) -> None:
        comments = [build_review_comment(body='Fix typo.'), build_review_comment(body='Add docs.')]
        text = review_comments_batch_text(comments)
        self.assertIn('1.', text)
        self.assertIn('2.', text)
        self.assertIn('Fix typo.', text)
        self.assertIn('Add docs.', text)

    def test_review_comments_batch_text_returns_empty_for_no_comments(self) -> None:
        self.assertEqual(review_comments_batch_text([]), '')


# ---------------------------------------------------------------------------
# Agents instruction utils
# ---------------------------------------------------------------------------

class AgentsInstructionUtilsTests(unittest.TestCase):
    def test_agents_instructions_for_path_returns_empty_for_missing_dir(self) -> None:
        result = agents_instructions_for_path('/nonexistent/path')
        self.assertEqual(result, '')

    def test_agents_instructions_for_path_returns_empty_string_for_blank(self) -> None:
        result = agents_instructions_for_path('')
        self.assertEqual(result, '')

    def test_agents_instructions_for_path_reads_agents_md(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_file = os.path.join(tmpdir, 'AGENTS.md')
            with open(agents_file, 'w') as f:
                f.write('Use pnpm for all package operations.')
            result = agents_instructions_for_path(tmpdir, repository_id='my-repo')
        self.assertIn('Agent safety', result)
        self.assertIn('Use pnpm for all package operations.', result)
        self.assertIn('my-repo', result)
        self.assertNotIn('Kato', result)

    def test_repository_agents_instructions_text_returns_empty_for_no_repos(self) -> None:
        self.assertEqual(repository_agents_instructions_text([]), '')

    def test_repository_agents_instructions_text_skips_repos_without_agents_md(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = types.SimpleNamespace(id='repo', local_path=tmpdir)
            result = repository_agents_instructions_text([repo])
        self.assertEqual(result, '')

    def test_repository_agents_instructions_text_reads_agents_md(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_file = os.path.join(tmpdir, 'AGENTS.md')
            with open(agents_file, 'w') as f:
                f.write('Run tests with pytest.')
            repo = types.SimpleNamespace(id='my-repo', local_path=tmpdir)
            result = repository_agents_instructions_text([repo])
        self.assertIn('Run tests with pytest.', result)
        self.assertNotIn('Kato safety', result)
        self.assertIn('Agent safety', result)


# ---------------------------------------------------------------------------
# Flow tests — A-Z
# ---------------------------------------------------------------------------

class OpenHandsFlowTests(unittest.TestCase):
    def _make_conversation_side_effect(
        self,
        conversation_id: str,
        summary: str,
        success: bool = True,
        branch_name: str = '',
    ):
        result_json = (
            f'{{"success": {str(success).lower()}, "summary": "{summary}"'
            + (f', "branch_name": "{branch_name}"' if branch_name else '')
            + '}'
        )
        return [
            mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
            mock_response(json_data=[{
                'id': 'start-1', 'status': 'READY',
                'app_conversation_id': conversation_id,
            }]),
            mock_response(json_data=[{'id': conversation_id, 'execution_status': 'finished'}]),
            mock_response(json_data={'items': [{
                'kind': 'MessageEvent', 'source': 'agent',
                'llm_message': {
                    'role': 'assistant',
                    'content': [{'text': result_json}],
                },
            }]}),
        ]

    def test_implement_task_full_flow(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token', max_retries=3,
        )
        task = build_task(task_id='FLOW-1', summary='Implement feature X', branch_name='feature/flow-1')

        post_side = [self._make_conversation_side_effect('conv-flow-1', 'Implemented X', branch_name='feature/flow-1')[0]]
        get_side = self._make_conversation_side_effect('conv-flow-1', 'Implemented X', branch_name='feature/flow-1')[1:]

        with patch.object(client, '_post', side_effect=post_side), \
                patch.object(client, '_get', side_effect=get_side), \
                patch.object(client, '_patch', return_value=mock_response()):
            result = client.implement_task(task)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Implemented X')
        self.assertEqual(result['branch_name'], 'feature/flow-1')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conv-flow-1')

    def test_test_task_full_flow(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        task = build_task(task_id='FLOW-2', summary='Test feature Y')

        post_side = [self._make_conversation_side_effect('conv-flow-2', 'Tests passed')[0]]
        get_side = self._make_conversation_side_effect('conv-flow-2', 'Tests passed')[1:]

        with patch.object(client, '_post', side_effect=post_side), \
                patch.object(client, '_get', side_effect=get_side), \
                patch.object(client, '_patch', return_value=mock_response()):
            result = client.test_task(task)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Tests passed')

    def test_fix_review_comment_full_flow(self) -> None:
        client = OpenHandsClient('https://openhands.example', 'oh-token')
        comment = build_review_comment(body='Rename this method.')

        post_side = [self._make_conversation_side_effect('conv-review-1', 'Renamed method')[0]]
        get_side = self._make_conversation_side_effect('conv-review-1', 'Renamed method')[1:]

        with patch.object(client, '_post', side_effect=post_side), \
                patch.object(client, '_get', side_effect=get_side), \
                patch.object(client, '_patch', return_value=mock_response()):
            result = client.fix_review_comment(comment, 'feature/proj-1')

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Renamed method')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'conv-review-1')

    def test_validate_then_implement_full_flow(self) -> None:
        client = OpenHandsClient(
            'https://openhands.example', 'oh-token',
            llm_settings={'llm_model': 'openai/gpt-4o'},
        )
        task = build_task(task_id='FLOW-4', summary='Build pipeline')

        count_response = mock_response(json_data=1)
        settings_response = mock_response()

        post_side = [
            settings_response,
            mock_response(json_data={'id': 'start-1', 'status': 'WORKING'}),
        ]
        get_side = [
            count_response,
            mock_response(json_data=[{
                'id': 'start-1', 'status': 'READY',
                'app_conversation_id': 'conv-flow-4',
            }]),
            mock_response(json_data=[{'id': 'conv-flow-4', 'execution_status': 'finished'}]),
            mock_response(json_data={'items': [{
                'kind': 'ActionEvent', 'source': 'agent', 'tool_name': 'finish',
                'tool_call': {'arguments': '{"success": true, "summary": "Built pipeline"}'},
            }]}),
        ]

        with patch.object(client, '_get', side_effect=get_side), \
                patch.object(client, '_post', side_effect=post_side), \
                patch.object(client, '_patch', return_value=mock_response()):
            client.validate_connection()
            result = client.implement_task(task)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'Built pipeline')


if __name__ == '__main__':
    unittest.main()
