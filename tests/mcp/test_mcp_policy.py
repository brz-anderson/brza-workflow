"""Testes da política (D4): o `brza.toml` por `tomllib` e por `parse_toml`, os padrões, a sobreposição, o
`enforce = false`, a sugestão à principal e o `brza mcp config` com o servidor de pé e parado.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import contextlib
import io
import os
import socket
import sys
import unittest
from unittest.mock import patch

from mcp_support import HomeCase
from tools_support import ToolsCase

from brza_mcp import cli, policy as pol  # noqa: E402

TOML = '''[mcp]
port = 4242

[orchestrator]
max_workers = 6

[policy]
enforce = true            # a chave de desligamento (M10)
suggest_to_main = true

[policy.roles.consultant]
model = "sonnet"
effort = "low"

[policy.roles.executor]
model = "haiku"
effort = "medium"

[policy.roles.architect]
model = "opus"
effort = "high"

[quota]
hard_cap_percent = 80
max_sessions_per_account = 3

[machine]
# min_free_mem_gb =

[resolver]
tev1_keep_alive = "2m"
# tev1_min_confidence =
haiku_max_budget_usd = 0.02   # o float que o parse_toml não lê
'''


class WorkspaceCase(HomeCase):
    def workspace(self, text=None):
        ws = self.tmp / 'ws'
        ws.mkdir(exist_ok=True)
        if text is not None:
            (ws / 'brza.toml').write_text(text)
        return str(ws)


class LoadTest(WorkspaceCase):
    def test_absent_file_gives_the_d4_defaults(self):
        config = pol.Policy(self.workspace()).config()
        self.assertEqual(config, pol.DEFAULTS)
        self.assertEqual(config['policy.roles.architect.model'], 'opus')
        self.assertEqual(config['quota.hard_cap_percent'], 90)
        self.assertNotIn('resolver.tev1_min_confidence', config)
        self.assertEqual(pol.Policy(None).config(), pol.DEFAULTS)

    def test_the_file_is_read_by_tomllib_and_by_parse_toml_with_the_same_result(self):
        ws = self.workspace(TOML)
        by_lib = pol.Policy(ws).config()
        by_parser = pol.Policy(ws, use_parse_toml=True).config()
        self.assertEqual(by_parser, by_lib)
        self.assertEqual(by_lib['policy.roles.executor.model'], 'haiku')
        self.assertEqual(by_lib['quota.hard_cap_percent'], 80)
        self.assertEqual(by_lib['resolver.haiku_max_budget_usd'], 0.02)
        self.assertEqual(by_lib['resolver.tev1_keep_alive'], '2m')
        self.assertEqual(by_lib['quota.soft_cap_percent'], 55)  # o que o arquivo não diz vem dos padrões

    def test_parse_toml_runs_when_tomllib_is_missing(self):  # o 3.10
        ws = self.workspace(TOML)
        with patch.dict(sys.modules, {'tomllib': None}):
            self.assertEqual(pol.parse_text(TOML)['resolver']['haiku_max_budget_usd'], 0.02)
            self.assertEqual(pol.Policy(ws).config()['policy.roles.executor.effort'], 'medium')

    def test_a_broken_file_is_an_error_that_names_the_file(self):
        ws = self.workspace('[policy\nenforce = true\n')
        for use_parse_toml in (False, True):
            with self.assertRaises(pol.PolicyError) as cm:
                pol.Policy(ws, use_parse_toml=use_parse_toml).config()
            self.assertIn('brza.toml', str(cm.exception))

    def test_the_override_wins_over_the_file_and_the_defaults(self):
        ws = self.workspace(TOML)
        overrides = {'quota.hard_cap_percent': 70, 'policy.enforce': False}
        config = pol.Policy(ws, overrides=lambda: overrides).config()
        self.assertEqual((config['quota.hard_cap_percent'], config['policy.enforce']), (70, False))
        self.assertEqual(config['quota.max_sessions_per_account'], 3)


class DecideModelTest(WorkspaceCase):
    def policy(self, text=None, overrides=None):
        return pol.Policy(self.workspace(text), overrides=lambda: overrides or {})

    def test_the_role_gives_model_and_effort_and_the_request_does_not_override_them(self):
        out = self.policy().decide_model({'role': 'architect', 'model': 'haiku', 'effort': 'low'})
        self.assertEqual((out['model'], out['effort'], out['reason']), ('opus', 'high', 'policy'))

    def test_the_request_counts_only_where_the_role_does_not_define_and_the_reason_says_so(self):
        text = '[policy.roles.executor]\nmodel = "sonnet"\n'
        policy = self.policy(text)
        config = {**pol.DEFAULTS}
        del config['policy.roles.executor.effort']
        with patch.object(pol.Policy, 'config', return_value=config):
            out = policy.decide_model({'role': 'executor', 'effort': 'xhigh'})
        self.assertEqual((out['model'], out['effort']), ('sonnet', 'xhigh'))
        self.assertIn('effort do pedido', out['reason'])

    def test_enforce_false_returns_the_request_without_imposition(self):
        out = self.policy(overrides={'policy.enforce': False}).decide_model(
            {'role': 'architect', 'model': 'haiku', 'effort': 'low'})
        self.assertEqual((out['model'], out['effort'], out['reason']), ('haiku', 'low', 'passthrough'))
        out = self.policy(TOML.replace('enforce = true', 'enforce = false')).decide_model({'role': 'executor'})
        self.assertEqual((out['model'], out['effort'], out['reason']), (None, None, 'passthrough'))

    def test_a_role_without_a_row_goes_to_the_tiebreaker_and_its_layer_picks_the_row(self):
        calls = []

        class Resolver:
            def classify(self, text):
                calls.append(text)
                return {'layer': 'alta', 'path': 'haiku', 'confidence': None, 'ms': 5}
        out = self.policy().decide_model({'role': 'mistério', 'task': 'desenhar X'}, Resolver())
        self.assertEqual(calls, ['desenhar X'])
        self.assertEqual((out['model'], out['effort']), ('opus', 'high'))
        self.assertEqual(out['reason'], 'resolver:haiku')
        self.assertEqual(out['resolver']['path'], 'haiku')

    def test_a_role_without_a_row_and_without_an_answer_stays_on_the_consultant(self):
        class Resolver:
            def classify(self, text):
                return {'layer': None, 'path': 'fallback'}
        for resolver in (None, Resolver()):
            out = self.policy().decide_model({'role': 'mistério', 'task': 'x'}, resolver)
            self.assertEqual((out['model'], out['effort'], out['reason']), ('sonnet', 'low', 'default_role'))

    def test_a_known_role_never_calls_the_tiebreaker(self):
        class Resolver:
            def classify(self, text):
                raise AssertionError('não devia chamar')
        self.assertEqual(self.policy().decide_model({'role': 'executor', 'task': 'x'}, Resolver())['model'], 'sonnet')


class SuggestionTest(WorkspaceCase, ToolsCase):
    def test_status_brings_the_suggestion_for_the_main_session_by_notice(self):
        self.workspace_dir = None
        _, out = self.call('status')
        suggestion = out['suggestion']
        self.assertEqual((suggestion['model'], suggestion['effort']), ('opus', 'high'))
        self.assertIn('sugere opus', suggestion['notice'])

    def test_the_key_turns_the_suggestion_off(self):
        self.call('config', {'key': 'policy.suggest_to_main', 'value': False})
        self.assertIsNone(self.call('status')[1]['suggestion'])
        self.call('config', {'key': 'policy.suggest_to_main', 'value': True})
        self.call('config', {'key': 'policy.enforce', 'value': False})
        self.assertIsNone(self.call('status')[1]['suggestion'])

    def test_dispatch_applies_the_table_and_the_override_of_enforce_turns_it_off(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'model': 'opus', 'effort': 'high'}, 'sess')
        row = self.queue.get(out['request'])
        self.assertEqual((row['model'], row['effort']), ('sonnet', 'low'))
        self.call('config', {'key': 'policy.enforce', 'value': False})
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'model': 'opus', 'effort': 'high'}, 'sess')
        row = self.queue.get(out['request'])
        self.assertEqual((row['model'], row['effort']), ('opus', 'high'))

    def test_the_defaults_feed_the_limits_and_an_override_wins(self):
        self.assertEqual(self.limits.get('orchestrator.max_workers'), 6)
        self.call('config', {'key': 'orchestrator.max_workers', 'value': 2})
        self.call('status')
        self.assertEqual(self.limits.get('orchestrator.max_workers'), 2)


class ConfigVerbTest(ToolsCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch.dict(os.environ, {'BRZA_MCP_PORT': str(self.port)}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                cli.main(['config', *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_set_get_list_and_clear_against_the_running_server(self):
        code, out, _ = self.run_cli('policy.enforce', 'false')
        self.assertEqual(code, 0)
        self.assertIn('false', out)
        self.assertEqual(self.service.get_overrides(), {'policy.enforce': False})
        self.assertIn('false', self.run_cli('policy.enforce')[1])
        self.assertIn('policy.enforce', self.run_cli()[1])
        self.run_cli('policy.enforce', 'null')
        self.assertEqual(self.service.get_overrides(), {})

    def test_a_server_error_exits_with_the_message(self):
        code, _, _ = self.run_cli('foo', '1')
        self.assertIn('chave de configuração desconhecida', str(code))


class ConfigVerbStoppedTest(HomeCase):
    def test_with_the_server_stopped_it_says_so_and_exits_3(self):
        with socket.socket() as s:  # uma porta que ninguém escuta
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        err = io.StringIO()
        with patch.dict(os.environ, {'BRZA_MCP_PORT': str(port)}), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                cli.main(['config', 'policy.enforce', 'false'])
        self.assertEqual(cm.exception.code, 3)
        self.assertIn('parado', err.getvalue())


if __name__ == '__main__':
    unittest.main()
