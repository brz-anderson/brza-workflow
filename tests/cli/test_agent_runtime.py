"""Contrato da porta AgentRuntime nos dois adaptadores, e o default de user_data() no macOS.

    python3 -m unittest tests.cli.test_agent_runtime
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'packages' / 'client' / 'bin'))
import agent_runtime  # noqa: E402
import brza_runtime  # noqa: E402
import cloud_runtime  # noqa: E402

CAPABILITIES = ('list', 'read', 'usage', 'open', 'send')


def invoke(runtime, name):
    if name == 'list':
        return runtime.list()
    if name == 'read':
        return runtime.read('sessao')
    if name == 'usage':
        return runtime.usage('sessao')
    if name == 'open':
        return runtime.open({})
    if name == 'send':
        return runtime.send('sessao', 'oi')
    raise AssertionError(name)


class ContractTest(unittest.TestCase):
    def runtimes(self):
        studio = agent_runtime.StudioRuntime(lister=lambda: [{
            'chat': 'claude_1', 'agent': 'claude', 'title': 'Aba', 'state': 'done', 'opened': '2026-11-14',
            'source': 'cloud'}])
        cloud = cloud_runtime.CloudRuntime({'CURSOR_CONVERSATION_ID': 'bc-teste'})
        return studio, cloud

    def test_both_adapters_declare_list_and_fail_the_rest_by_name(self):
        for runtime in self.runtimes():
            declared = runtime.capabilities()
            self.assertIn('list', declared)
            self.assertTrue(set(declared) <= set(CAPABILITIES))
            for name in CAPABILITIES:
                if name in declared:
                    self.assertTrue(callable(getattr(runtime, name)))
                    continue
                with self.subTest(runtime=runtime.name, capability=name):
                    with self.assertRaises(agent_runtime.MissingCapability) as caught:
                        invoke(runtime, name)
                    message = str(caught.exception)
                    self.assertIn(runtime.name, message)
                    self.assertIn(name, message)

    def test_cloud_declares_list_and_read_and_studio_declares_all_five(self):
        studio, cloud = self.runtimes()
        self.assertEqual(cloud.capabilities(), frozenset({'list', 'read'}))
        self.assertEqual(studio.capabilities(), frozenset({'list', 'read', 'usage', 'open', 'send'}))

    def test_cloud_read_marks_the_transcript_ausente(self):
        env = {'CURSOR_CONVERSATION_ID': 'bc-teste'}
        before = dict(env)
        record = cloud_runtime.CloudRuntime(env).read('nome')
        self.assertEqual(env, before)
        self.assertEqual(record['transcript'], 'ausente')
        self.assertEqual(record['source'], 'cloud')
        self.assertNotEqual(record['transcript'], 0)

    def test_cloud_list_is_the_conversation_and_does_not_invent_orca_or_a_zero(self):
        env = {'CURSOR_CONVERSATION_ID': 'bc-teste'}
        before = dict(env)
        records = cloud_runtime.CloudRuntime(env).list()
        self.assertEqual(env, before)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, 'bc-teste')
        self.assertEqual(record.source, 'cloud')
        self.assertEqual(record.agent, 'cursor')
        self.assertEqual(record.metrics['cost_usd'], {'value': 'ausente', 'origin': 'ausente'})
        self.assertEqual(record.metrics['tokens'], {'value': 'ausente', 'origin': 'ausente'})
        self.assertNotEqual(record.metrics['cost_usd']['value'], 0)
        self.assertNotEqual(record.metrics['tokens']['value'], 0)

    def test_studio_list_never_emits_a_cloud_record(self):
        (record,) = agent_runtime.StudioRuntime(lister=lambda: [{
            'chat': 'claude_1', 'id': 'bc-nao', 'source': 'cloud', 'agent': 'claude', 'title': 'Aba',
            'state': 'working', 'opened': '2026-11-14'}]).list()
        self.assertEqual(record.source, 'studio')
        self.assertNotEqual(record.source, 'cloud')

    def test_open_argv_is_agent_p_without_force(self):
        first = cloud_runtime.open_argv('grok-4.7', '/tmp/reuniao')
        self.assertEqual(first, ['agent', '-p', '--output-format', 'json', '--model', 'grok-4.7',
                                 '--workspace', '/tmp/reuniao', '--trust'])
        self.assertNotIn('--resume', first)
        self.assertNotIn('--force', first)
        resumed = cloud_runtime.open_argv('grok-4.7', '/tmp/reuniao', 'chat-1')
        self.assertEqual(resumed, first + ['--resume', 'chat-1'])

    def test_selection_prefers_the_socket_and_honors_the_force_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'ORCA_USER_DATA_PATH': tmp, 'CURSOR_CONVERSATION_ID': 'bc-teste'}):
                self.assertEqual(agent_runtime.select_runtime().name, 'cloud')
                self.assertEqual(agent_runtime.select_runtime('studio').name, 'studio')
                (Path(tmp) / 'orca-runtime.json').write_text('{}')
                self.assertEqual(agent_runtime.select_runtime().name, 'studio')
                self.assertEqual(agent_runtime.select_runtime('cloud').name, 'cloud')
                (Path(tmp) / 'orca-runtime.json').unlink()
            with patch.dict(os.environ, {'ORCA_USER_DATA_PATH': tmp, 'CURSOR_CONVERSATION_ID': ''}):
                self.assertEqual(agent_runtime.select_runtime().name, 'studio')

    def test_user_data_without_the_env_stays_the_macos_default(self):
        env = {key: value for key, value in os.environ.items() if key != 'ORCA_USER_DATA_PATH'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(brza_runtime.user_data(),
                             Path.home() / 'Library' / 'Application Support' / 'brza-studio')


if __name__ == '__main__':
    unittest.main()
