import unittest
from unittest import mock

from agent_core_lib.agent_core_lib.helpers.command_policy import (
    CONFIGURABLE_CATEGORIES,
    Decision,
    RiskCategory,
)
from kato_core_lib.helpers import kato_settings_schema_utils as schema
from kato_core_lib.helpers import action_guard_config as cfg


class ActionGuardSchemaTests(unittest.TestCase):
    def test_secure_defaults_cover_enabled_plus_every_category(self):
        self.assertEqual(
            len(schema.ACTION_GUARD_SECURE_DEFAULTS),
            len(CONFIGURABLE_CATEGORIES) + 1,
        )
        self.assertEqual(schema.ACTION_GUARD_SECURE_DEFAULTS['KATO_ACTION_GUARD_ENABLED'], 'true')

    def test_all_keys_are_in_the_write_whitelist(self):
        keys = schema.all_settings_keys()
        for env_key in schema.ACTION_GUARD_SECURE_DEFAULTS:
            self.assertIn(env_key, keys, env_key)

    def test_validation_accepts_valid_and_rejects_invalid(self):
        self.assertEqual(
            schema.validate_settings_values({'KATO_ACTION_GUARD_CREDENTIAL_READ': 'block'}),
            [],
        )
        self.assertTrue(
            schema.validate_settings_values({'KATO_ACTION_GUARD_CREDENTIAL_READ': 'maybe'}),
        )
        self.assertTrue(
            schema.validate_settings_values({'KATO_ACTION_GUARD_ENABLED': 'yes'}),
        )

    def test_select_fields_have_no_empty_option(self):
        # Memory: no ambiguous "Auto"/"(default)" picker — every Action Guard
        # select must offer ONLY concrete block/ask/allow values.
        section = next(s for s in schema.schema_for_api() if s['id'] == 'action_guard')
        selects = [f for f in section['fields'] if f['type'] == 'select']
        self.assertEqual(len(selects), len(CONFIGURABLE_CATEGORIES))
        for field in selects:
            self.assertEqual(field['options'], ['block', 'ask', 'allow'])

    def test_defaults_match_engine_secure_default(self):
        self.assertEqual(schema.ACTION_GUARD_SECURE_DEFAULTS['KATO_ACTION_GUARD_CREDENTIAL_READ'], 'block')
        self.assertEqual(schema.ACTION_GUARD_SECURE_DEFAULTS['KATO_ACTION_GUARD_DESTRUCTIVE_FS'], 'ask')


class ResolverTests(unittest.TestCase):
    def setUp(self):
        # Default: no settings.json content (resolver reads it live).
        patcher = mock.patch.object(cfg, 'read_kato_settings', return_value={})
        self.read_settings = patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_env_yields_secure_defaults(self):
        policy = cfg.resolve_action_guard_policy(env={})
        self.assertEqual(policy.decide(RiskCategory.CREDENTIAL_READ), Decision.BLOCK)
        self.assertEqual(policy.decide(RiskCategory.DESTRUCTIVE_FS), Decision.ASK)
        self.assertTrue(policy.enabled)

    def test_env_override(self):
        policy = cfg.resolve_action_guard_policy(
            env={'KATO_ACTION_GUARD_CREDENTIAL_READ': 'ask'},
        )
        self.assertEqual(policy.decide(RiskCategory.CREDENTIAL_READ), Decision.ASK)

    def test_settings_json_wins_over_env(self):
        self.read_settings.return_value = {'KATO_ACTION_GUARD_CREDENTIAL_READ': 'allow'}
        policy = cfg.resolve_action_guard_policy(
            env={'KATO_ACTION_GUARD_CREDENTIAL_READ': 'ask'},
        )
        self.assertEqual(policy.decide(RiskCategory.CREDENTIAL_READ), Decision.ALLOW)

    def test_floor_cannot_be_loosened_via_settings(self):
        self.read_settings.return_value = {'KATO_ACTION_GUARD_REMOTE_EXEC': 'allow'}
        policy = cfg.resolve_action_guard_policy(env={})
        self.assertEqual(policy.decide(RiskCategory.REMOTE_EXEC), Decision.BLOCK)

    def test_enabled_toggle(self):
        policy = cfg.resolve_action_guard_policy(env={'KATO_ACTION_GUARD_ENABLED': 'false'})
        self.assertFalse(policy.enabled)

    def test_bad_settings_file_falls_back_to_secure_default(self):
        self.read_settings.side_effect = RuntimeError('corrupt json')
        policy = cfg.resolve_action_guard_policy(env={})
        # Never raises; resolves to the secure default.
        self.assertEqual(policy.decide(RiskCategory.CREDENTIAL_READ), Decision.BLOCK)
        self.assertTrue(policy.enabled)

    def test_posture_reports_resolved_values(self):
        self.read_settings.return_value = {'KATO_ACTION_GUARD_PERSISTENCE': 'block'}
        posture = cfg.action_guard_posture(env={})
        self.assertEqual(posture['KATO_ACTION_GUARD_PERSISTENCE'], 'block')
        self.assertEqual(posture['KATO_ACTION_GUARD_CREDENTIAL_READ'], 'block')  # default


class PostureBannerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cfg, 'read_kato_settings', return_value={})
        self.read_settings = patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_banner_is_enabled_with_per_category_rows(self):
        lines = cfg.action_guard_posture_lines(env={})
        text = '\n'.join(lines)
        self.assertIn('enabled               : true', text)
        self.assertIn('credential_read', text)
        self.assertIn('audit log', text)
        self.assertNotIn('WARNING', text)

    def test_high_risk_allow_emits_warning(self):
        lines = cfg.action_guard_posture_lines(
            env={'KATO_ACTION_GUARD_CREDENTIAL_READ': 'allow'},
        )
        self.assertTrue(any('WARNING' in ln and 'credential_read' in ln for ln in lines))

    def test_disabled_emits_off_warning_and_hides_rows(self):
        lines = cfg.action_guard_posture_lines(
            env={'KATO_ACTION_GUARD_ENABLED': 'false'},
        )
        text = '\n'.join(lines)
        self.assertIn('enabled               : false', text)
        self.assertTrue(any('OFF' in ln for ln in lines))
        # category rows are hidden when the guard is disabled
        self.assertNotIn('  credential_read      :', text)

    def test_print_writes_to_stderr(self):
        import io
        buffer = io.StringIO()
        cfg.print_action_guard_posture(env={}, stderr=buffer)
        out = buffer.getvalue()
        self.assertIn('Action Guard', out)
        self.assertIn('=' * 78, out)


if __name__ == '__main__':
    unittest.main()
