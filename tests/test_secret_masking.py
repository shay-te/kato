"""Tests for the config-display masking helper.

Pin both the classification rule (which key names trigger
masking) and the redacted format. A regression here would mean
either:
  - secret values land in boot logs in plaintext (key drift),
  - or legitimate config values get incorrectly masked, making
    operators chase "where did my value go?" debugging.
"""

from __future__ import annotations

import unittest

from kato_core_lib.helpers.secret_masking_utils import (
    is_secret_key,
    mask_for_display,
    mask_value,
)


class IsSecretKeyTests(unittest.TestCase):

    def test_classic_secret_names_are_recognized(self) -> None:
        for key in [
            'password', 'PASSWORD', 'Password',
            'token', 'TOKEN', 'access_token',
            'secret', 'SECRET', 'client_secret',
            'api_key', 'API_KEY', 'apiKey', 'apikey',
            'private_key', 'private-key', 'privateKey',
            'app_password', 'BITBUCKET_APP_PASSWORD',
            'authorization', 'Authorization',
            'bearer', 'Bearer',
            'credential', 'credentials', 'aws_credentials',
        ]:
            self.assertTrue(
                is_secret_key(key),
                f'{key!r} should be recognized as a secret-shaped key',
            )

    def test_innocent_names_are_NOT_flagged(self) -> None:
        for key in [
            'username', 'email', 'project', 'task_id',
            'repository', 'branch', 'message', 'title',
            'cwd', 'path', 'url',
            'count', 'enabled', 'timeout_seconds',
        ]:
            self.assertFalse(
                is_secret_key(key),
                f'{key!r} should NOT be flagged as a secret-shaped key',
            )

    def test_empty_or_none_key_is_not_secret(self) -> None:
        # Defensive: never crash on bad input.
        self.assertFalse(is_secret_key(''))
        self.assertFalse(is_secret_key(None))

    def test_substring_match_is_case_insensitive(self) -> None:
        # ``Auth_token``, ``MY_API_KEY``, ``CLIENT-SECRET`` all
        # match regardless of casing.
        self.assertTrue(is_secret_key('Auth_token'))
        self.assertTrue(is_secret_key('MY_API_KEY'))
        self.assertTrue(is_secret_key('CLIENT-SECRET'))

    def test_does_not_over_match_on_key_alone(self) -> None:
        # The string ``key`` by itself in a field name is too
        # common to flag. Only compound forms (``api_key``,
        # ``private_key``, ``access_key``, ``secret_key``) should
        # match. ``primary_key``, ``foreign_key``, ``map_key``,
        # ``key`` itself must NOT be flagged.
        self.assertFalse(is_secret_key('primary_key'))
        self.assertFalse(is_secret_key('foreign_key'))
        self.assertFalse(is_secret_key('map_key'))
        self.assertFalse(is_secret_key('key'))


class MaskValueTests(unittest.TestCase):

    def test_short_value_fully_masked(self) -> None:
        # Less than prefix+suffix+4 chars → no useful prefix/suffix
        # to show without exposing the whole thing. Fully masked.
        self.assertEqual(mask_value('short'), '****')
        self.assertEqual(mask_value('abc'), '****')
        self.assertEqual(mask_value('1'), '****')

    def test_long_value_keeps_prefix_and_suffix(self) -> None:
        # 20-char value: keep 4 prefix + 4 suffix, mask middle.
        result = mask_value('abcd1234567890wxyz12')
        self.assertTrue(result.startswith('abcd'))
        self.assertTrue(result.endswith('yz12'))
        self.assertIn('****', result)

    def test_empty_value_returns_empty(self) -> None:
        # Operators distinguish "no value set" from "redacted".
        self.assertEqual(mask_value(''), '')
        self.assertEqual(mask_value(None), '')

    def test_custom_prefix_suffix_keep(self) -> None:
        # Caller can ask for less surface (e.g. 2+2) for shorter
        # tokens where 4+4 would leave only 4 hidden chars.
        result = mask_value('abcd1234567890wxyz', prefix_keep=2, suffix_keep=2)
        self.assertTrue(result.startswith('ab'))
        self.assertTrue(result.endswith('yz'))


class MaskForDisplayTests(unittest.TestCase):

    def test_non_secret_key_returns_value_unchanged(self) -> None:
        self.assertEqual(mask_for_display('username', 'alice'), 'alice')
        self.assertEqual(mask_for_display('task_id', 'PROJ-1'), 'PROJ-1')
        self.assertEqual(mask_for_display('repository', 'client'), 'client')

    def test_secret_key_with_long_value_is_masked(self) -> None:
        result = mask_for_display('api_key', 'sk-abc123xyz456SUPER_SECRET_END')
        self.assertNotIn('SUPER_SECRET', result)
        self.assertTrue(result.startswith('sk-a'))
        self.assertIn('****', result)

    def test_secret_key_with_short_value_is_fully_masked(self) -> None:
        # A 6-char "secret" gets the full mask — no prefix/suffix
        # surface to safely show.
        self.assertEqual(mask_for_display('token', 'abc123'), '****')

    def test_secret_key_with_empty_value_returns_empty(self) -> None:
        # Operators see "not set" vs "redacted" distinctly.
        self.assertEqual(mask_for_display('password', ''), '')
        self.assertEqual(mask_for_display('password', None), '')

    def test_real_world_kato_env_vars(self) -> None:
        # The exact env-var names kato reads — pin that each is
        # correctly recognized as secret-shaped.
        for key, value in [
            ('BITBUCKET_APP_PASSWORD', 'ATBB1234567890abc'),
            ('GITHUB_TOKEN', 'ghp_1234567890abcdef1234'),
            ('GITLAB_TOKEN', 'glpat-xxxxxxxxxxxxxxxxxxxx'),
            ('YOUTRACK_TOKEN', 'perm:abcd-token-xyz'),
            ('JIRA_API_TOKEN', 'jira_api_token_value_123'),
            ('ANTHROPIC_API_KEY', 'sk-ant-api03-zzz'),
        ]:
            masked = mask_for_display(key, value)
            self.assertNotEqual(
                masked, value,
                f'{key} value was NOT masked — secret leaks in logs',
            )

    def test_non_string_value_is_stringified_before_masking(self) -> None:
        # Defensive: caller might pass an int / dict / None.
        # Stringification then mask — never crash.
        result = mask_for_display('token', 12345678901234567890)
        self.assertIn('****', result)

    def test_unicode_value_round_trips_cleanly(self) -> None:
        # Emoji / non-ASCII in a "secret" value still mask correctly.
        result = mask_for_display('api_key', 'sk-🔑-abc1234567890-end-xyz')
        self.assertIn('****', result)
        self.assertNotIn('abc1234567890', result)


if __name__ == '__main__':
    unittest.main()
