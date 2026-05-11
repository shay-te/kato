from __future__ import annotations

import unittest
from unittest.mock import patch

from openrouter_core_lib.openrouter_core_lib import OpenRouterClient
from tests.utils import assert_client_headers_and_timeout, mock_response


# ---------------------------------------------------------------------------
# OpenRouterClient — init
# ---------------------------------------------------------------------------

class OpenRouterClientInitTests(unittest.TestCase):
    def test_uses_default_max_retries(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        self.assertEqual(client.max_retries, 3)

    def test_uses_custom_max_retries(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key', max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_timeout_is_30(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        assert_client_headers_and_timeout(self, client, 'or-key', 30)

    def test_provider_name(self) -> None:
        self.assertEqual(OpenRouterClient.provider_name, 'openrouter')


# ---------------------------------------------------------------------------
# OpenRouterClient — validate_connection
# ---------------------------------------------------------------------------

class OpenRouterClientValidateConnectionTests(unittest.TestCase):
    def test_checks_models_endpoint(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={'data': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()
        mock_get.assert_called_once_with('/models')

    def test_raises_when_server_returns_error(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        error_response = mock_response(status_code=401)
        error_response.raise_for_status.side_effect = RuntimeError('401 Unauthorized')

        with patch.object(client, '_get', return_value=error_response):
            with self.assertRaises(RuntimeError):
                client.validate_connection()

        error_response.raise_for_status.assert_called_once_with()

    def test_does_not_inspect_response_body(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data=None)

        with patch.object(client, '_get', return_value=response):
            client.validate_connection()

        response.raise_for_status.assert_called_once_with()


# ---------------------------------------------------------------------------
# OpenRouterClient — validate_model_available
# ---------------------------------------------------------------------------

class OpenRouterClientValidateModelAvailableTests(unittest.TestCase):
    def _client_with_models(self, model_list: list) -> tuple:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={'data': model_list})
        return client, response

    def test_accepts_model_found_by_id_field(self) -> None:
        client, response = self._client_with_models([
            {'id': 'openai/gpt-4o-mini'},
            {'id': 'anthropic/claude-3.5-haiku'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openrouter/openai/gpt-4o-mini')

        response.raise_for_status.assert_called_once_with()

    def test_accepts_model_found_by_name_field(self) -> None:
        client, response = self._client_with_models([
            {'name': 'openai/gpt-4o'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openai/gpt-4o')

    def test_accepts_model_found_by_slug_field(self) -> None:
        client, response = self._client_with_models([
            {'slug': 'anthropic/claude-opus-4'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('anthropic/claude-opus-4')

    def test_accepts_model_found_by_model_field(self) -> None:
        client, response = self._client_with_models([
            {'model': 'meta-llama/llama-3.1-8b'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('meta-llama/llama-3.1-8b')

    def test_strips_openrouter_prefix_before_lookup(self) -> None:
        client, response = self._client_with_models([
            {'id': 'anthropic/claude-3.5-haiku'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openrouter/anthropic/claude-3.5-haiku')

    def test_rejects_model_not_in_list(self) -> None:
        client, response = self._client_with_models([
            {'id': 'openai/gpt-4o-mini'},
        ])
        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(
                RuntimeError,
                'OpenRouter model not available: anthropic/claude-3.5-haiku',
            ):
                client.validate_model_available('openrouter/anthropic/claude-3.5-haiku')

    def test_rejects_model_not_in_empty_list(self) -> None:
        client, response = self._client_with_models([])
        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'OpenRouter model not available'):
                client.validate_model_available('openai/gpt-4o')

    def test_skips_validation_for_blank_model(self) -> None:
        client, response = self._client_with_models([])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('')

        response.raise_for_status.assert_called_once_with()

    def test_skips_validation_for_whitespace_model(self) -> None:
        client, response = self._client_with_models([])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('   ')

        response.raise_for_status.assert_called_once_with()

    def test_raises_when_payload_is_not_dict(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data=[{'id': 'openai/gpt-4o'}])
        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'OpenRouter model not available'):
                client.validate_model_available('openai/gpt-4o')

    def test_raises_when_data_field_is_not_list(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={'data': 'not-a-list'})
        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'OpenRouter model not available'):
                client.validate_model_available('openai/gpt-4o')

    def test_raises_when_data_field_missing(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={})
        with patch.object(client, '_get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'OpenRouter model not available'):
                client.validate_model_available('openai/gpt-4o')

    def test_skips_non_dict_entries_in_data(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        response = mock_response(json_data={
            'data': [
                'not-a-dict',
                None,
                42,
                {'id': 'openai/gpt-4o'},
            ]
        })
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openai/gpt-4o')

    def test_model_without_openrouter_prefix_accepted_when_in_list(self) -> None:
        client, response = self._client_with_models([{'id': 'openai/gpt-4o'}])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openai/gpt-4o')

    def test_model_with_multiple_field_matches_uses_any(self) -> None:
        client, response = self._client_with_models([
            {'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'slug': 'gpt-4o-slug'},
        ])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openai/gpt-4o')
            client.validate_model_available('GPT-4o')
            client.validate_model_available('gpt-4o-slug')

    def test_calls_raise_for_status(self) -> None:
        client, response = self._client_with_models([{'id': 'openai/gpt-4o'}])
        with patch.object(client, '_get', return_value=response):
            client.validate_model_available('openai/gpt-4o')
        response.raise_for_status.assert_called_once_with()


# ---------------------------------------------------------------------------
# OpenRouterClient — _available_model_ids
# ---------------------------------------------------------------------------

class OpenRouterClientAvailableModelIdsTests(unittest.TestCase):
    def test_returns_empty_for_non_dict_payload(self) -> None:
        self.assertEqual(OpenRouterClient._available_model_ids([]), set())
        self.assertEqual(OpenRouterClient._available_model_ids(None), set())
        self.assertEqual(OpenRouterClient._available_model_ids('string'), set())

    def test_returns_empty_when_data_key_missing(self) -> None:
        self.assertEqual(OpenRouterClient._available_model_ids({}), set())

    def test_returns_empty_when_data_is_not_list(self) -> None:
        self.assertEqual(OpenRouterClient._available_model_ids({'data': 'nope'}), set())
        self.assertEqual(OpenRouterClient._available_model_ids({'data': None}), set())
        self.assertEqual(OpenRouterClient._available_model_ids({'data': {}}), set())

    def test_returns_empty_for_empty_data_list(self) -> None:
        self.assertEqual(OpenRouterClient._available_model_ids({'data': []}), set())

    def test_collects_id_field(self) -> None:
        result = OpenRouterClient._available_model_ids({'data': [{'id': 'openai/gpt-4o'}]})
        self.assertIn('openai/gpt-4o', result)

    def test_collects_name_field(self) -> None:
        result = OpenRouterClient._available_model_ids({'data': [{'name': 'GPT-4o'}]})
        self.assertIn('GPT-4o', result)

    def test_collects_slug_field(self) -> None:
        result = OpenRouterClient._available_model_ids({'data': [{'slug': 'gpt4o'}]})
        self.assertIn('gpt4o', result)

    def test_collects_model_field(self) -> None:
        result = OpenRouterClient._available_model_ids({'data': [{'model': 'meta/llama'}]})
        self.assertIn('meta/llama', result)

    def test_collects_all_fields_from_single_entry(self) -> None:
        result = OpenRouterClient._available_model_ids({
            'data': [{'id': 'a', 'name': 'b', 'slug': 'c', 'model': 'd'}]
        })
        self.assertEqual(result, {'a', 'b', 'c', 'd'})

    def test_collects_from_multiple_entries(self) -> None:
        result = OpenRouterClient._available_model_ids({
            'data': [{'id': 'openai/gpt-4o'}, {'id': 'anthropic/claude-3.5-haiku'}]
        })
        self.assertEqual(result, {'openai/gpt-4o', 'anthropic/claude-3.5-haiku'})

    def test_skips_non_dict_entries(self) -> None:
        result = OpenRouterClient._available_model_ids({
            'data': ['string', None, 42, {'id': 'openai/gpt-4o'}]
        })
        self.assertEqual(result, {'openai/gpt-4o'})

    def test_skips_empty_field_values(self) -> None:
        result = OpenRouterClient._available_model_ids({
            'data': [{'id': '', 'name': None, 'slug': '   '}]
        })
        self.assertEqual(result, set())

    def test_normalizes_whitespace_from_field_values(self) -> None:
        result = OpenRouterClient._available_model_ids({
            'data': [{'id': '  openai/gpt-4o  '}]
        })
        self.assertIn('openai/gpt-4o', result)


# ---------------------------------------------------------------------------
# OpenRouterClient — _normalized_model_name
# ---------------------------------------------------------------------------

class OpenRouterClientNormalizedModelNameTests(unittest.TestCase):
    def test_strips_openrouter_prefix(self) -> None:
        result = OpenRouterClient._normalized_model_name('openrouter/openai/gpt-4o')
        self.assertEqual(result, 'openai/gpt-4o')

    def test_keeps_model_without_prefix(self) -> None:
        result = OpenRouterClient._normalized_model_name('openai/gpt-4o')
        self.assertEqual(result, 'openai/gpt-4o')

    def test_strips_whitespace(self) -> None:
        result = OpenRouterClient._normalized_model_name('  openai/gpt-4o  ')
        self.assertEqual(result, 'openai/gpt-4o')

    def test_returns_empty_for_blank(self) -> None:
        self.assertEqual(OpenRouterClient._normalized_model_name(''), '')
        self.assertEqual(OpenRouterClient._normalized_model_name('   '), '')

    def test_strips_openrouter_prefix_only_at_start(self) -> None:
        result = OpenRouterClient._normalized_model_name('openrouter/openrouter/nested')
        self.assertEqual(result, 'openrouter/nested')

    def test_model_that_only_has_prefix(self) -> None:
        result = OpenRouterClient._normalized_model_name('openrouter/')
        self.assertEqual(result, '')


# ---------------------------------------------------------------------------
# Flow tests — A-Z
# ---------------------------------------------------------------------------

class OpenRouterClientFlowTests(unittest.TestCase):
    def test_validate_connection_then_model_check_passes(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key', max_retries=2)
        models_response = mock_response(json_data={
            'data': [
                {'id': 'openai/gpt-4o-mini'},
                {'id': 'anthropic/claude-3.5-haiku'},
                {'name': 'openrouter/meta-llama/llama-3.1-8b'},
            ]
        })

        with patch.object(client, '_get', return_value=models_response):
            client.validate_connection()

        with patch.object(client, '_get', return_value=models_response):
            client.validate_model_available('openrouter/openai/gpt-4o-mini')

        self.assertEqual(models_response.raise_for_status.call_count, 2)

    def test_validate_connection_then_model_check_fails_for_unknown_model(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        models_response = mock_response(json_data={'data': [{'id': 'openai/gpt-4o'}]})

        with patch.object(client, '_get', return_value=models_response):
            client.validate_connection()

        with patch.object(client, '_get', return_value=models_response):
            with self.assertRaisesRegex(
                RuntimeError,
                'OpenRouter model not available: anthropic/claude-opus-4',
            ):
                client.validate_model_available('openrouter/anthropic/claude-opus-4')

    def test_validate_connection_failure_propagates(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        error_response = mock_response(status_code=403)
        error_response.raise_for_status.side_effect = RuntimeError('403 Forbidden')

        with patch.object(client, '_get', return_value=error_response):
            with self.assertRaisesRegex(RuntimeError, '403 Forbidden'):
                client.validate_connection()

    def test_full_model_validation_flow_with_multiple_models(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key', max_retries=3)
        models_response = mock_response(json_data={
            'data': [
                {'id': 'openai/gpt-4o-mini', 'name': 'GPT-4o Mini', 'slug': 'gpt-4o-mini'},
                {'id': 'anthropic/claude-3.5-haiku'},
                {'id': 'meta-llama/llama-3.1-8b'},
            ]
        })

        with patch.object(client, '_get', return_value=models_response) as mock_get:
            client.validate_model_available('openai/gpt-4o-mini')
            client.validate_model_available('GPT-4o Mini')
            client.validate_model_available('gpt-4o-mini')
            client.validate_model_available('anthropic/claude-3.5-haiku')
            client.validate_model_available('openrouter/meta-llama/llama-3.1-8b')

        self.assertEqual(mock_get.call_count, 5)

    def test_blank_model_is_always_accepted_regardless_of_available_list(self) -> None:
        client = OpenRouterClient('https://openrouter.ai/api/v1', 'or-key')
        empty_response = mock_response(json_data={'data': []})

        with patch.object(client, '_get', return_value=empty_response):
            client.validate_model_available('')
            client.validate_model_available('openrouter/')


if __name__ == '__main__':
    unittest.main()
