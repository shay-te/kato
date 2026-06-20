import tempfile
import unittest
from pathlib import Path

from kato_core_lib.helpers import upgrade_safety_utils as usu


class PersistenceHealthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        # Claude transcripts live under a projects root the resolver honours
        # via CLAUDE_SESSIONS_ROOT.
        self.projects = self.home / 'claude-projects'
        (self.projects / 'enc-cwd').mkdir(parents=True)
        self.sessions = self.home / '.kato' / 'sessions'
        self.sessions.mkdir(parents=True)

    def _health(self, env_extra=None):
        env = {'CLAUDE_SESSIONS_ROOT': str(self.projects)}
        env.update(env_extra or {})
        return usu.persistence_health(env=env, home=self.home)

    def test_counts_chats_sessions_and_credentials(self):
        (self.projects / 'enc-cwd' / 's1.jsonl').write_text('{}\n')
        (self.projects / 'enc-cwd' / 's2.jsonl').write_text('{}\n')
        (self.sessions / 'UNA-1.json').write_text('{}')
        health = self._health({'ANTHROPIC_API_KEY': 'sk-ant-xxx'})
        self.assertTrue(health['chats']['present'])
        self.assertEqual(health['chats']['count'], 2)
        self.assertTrue(health['sessions']['present'])
        self.assertEqual(health['sessions']['count'], 1)
        self.assertTrue(health['host_credentials']['present'])
        self.assertIn('ANTHROPIC_API_KEY', health['host_credentials']['sources'])

    def test_detects_claude_login_file_as_credential_source(self):
        (self.home / '.claude.json').write_text('{}')
        health = self._health()
        self.assertTrue(health['host_credentials']['present'])
        self.assertIn('~/.claude.json', health['host_credentials']['sources'])

    def test_empty_store_reports_absent_not_error(self):
        health = self._health()
        self.assertFalse(health['chats']['present'])
        self.assertEqual(health['chats']['count'], 0)
        self.assertFalse(health['sessions']['present'])
        self.assertFalse(health['host_credentials']['present'])

    def test_oauth_token_counts_as_credential(self):
        health = self._health({'CLAUDE_CODE_OAUTH_TOKEN': 'tok'})
        self.assertEqual(health['host_credentials']['sources'], ['CLAUDE_CODE_OAUTH_TOKEN'])

    def test_lines_render_counts_and_the_do_not_delete_note(self):
        (self.projects / 'enc-cwd' / 's1.jsonl').write_text('{}\n')
        lines = usu.persistence_health_lines(
            env={'CLAUDE_SESSIONS_ROOT': str(self.projects)}, home=self.home,
        )
        text = '\n'.join(lines)
        self.assertIn('chats (transcripts)', text)
        self.assertIn('kato sessions', text)
        self.assertIn('NOT touched', text)
        self.assertIn('kato-claude-config', text)

    def test_never_raises_on_missing_dirs(self):
        # No projects dir, no ~/.kato — must still return a valid shape.
        health = usu.persistence_health(
            env={'CLAUDE_SESSIONS_ROOT': str(self.home / 'nope')},
            home=self.home / 'also-nope',
        )
        self.assertEqual(health['chats']['count'], 0)
        self.assertEqual(health['sessions']['count'], 0)


if __name__ == '__main__':
    unittest.main()
