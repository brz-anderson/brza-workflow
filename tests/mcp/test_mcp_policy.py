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
from pathlib import Path
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
    def test_the_suggestion_is_born_off_until_there_is_a_signal_of_what_the_main_session_does(self):  # P4 O-A7
        self.assertFalse(pol.DEFAULTS['policy.suggest_to_main'])
        self.assertIsNone(self.call('status')[1]['suggestion'])

    def test_status_brings_the_suggestion_for_the_main_session_by_notice(self):
        self.workspace_dir = None
        self.call('config', {'key': 'policy.suggest_to_main', 'value': True})
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


class BrokenTomlTest(WorkspaceCase, HomeCase):  # P4 O-A1, O-A2
    def service(self, text='[mcp\nport = '):
        from brza_mcp import limits as lim, queue as q, tools
        from brza_mcp.registry import ToolRegistry
        from brza_mcp.telemetry import Telemetry
        self.registry = ToolRegistry()
        self.queue = q.Queue(':memory:')
        self.addCleanup(self.queue.close)
        self.ws = self.workspace(text)
        return tools.install(self.registry, queue=self.queue, limits=lim.Limits(machine=lambda: {}), workspace=self.ws,
                             index_reader=lambda w: '', telemetry=Telemetry(None))

    def call(self, name, args=None):
        from brza_mcp.registry import CallContext
        out = self.registry.call(name, args or {}, CallContext('s1'))
        return out.get('isError', False), out['content'][0]['text']

    def test_a_broken_brza_toml_does_not_stop_the_service_from_starting_and_each_tool_names_the_file(self):
        service = self.service()
        self.assertEqual(service.limits.get('orchestrator.max_workers'), 6)  # sobe com os padrões
        for name, args in (('status', {}), ('dispatch', {'task': 't', 'role': 'consultant'}), ('config', {})):
            is_error, text = self.call(name, args)
            self.assertTrue(is_error, name)
            self.assertIn(str(Path(self.ws) / 'brza.toml'), text)
        self.assertEqual(self.queue.pending(), [])

    def test_config_does_not_write_and_answer_an_error_at_once(self):
        service = self.service()
        is_error, _ = self.call('config', {'key': 'policy.enforce', 'value': False})
        self.assertTrue(is_error)
        self.assertEqual(service.get_overrides(), {})

    def test_the_tools_come_back_when_the_file_is_fixed(self):
        self.service()
        (Path(self.ws) / 'brza.toml').write_text('[orchestrator]\nmax_workers = 2\n')
        self.assertFalse(self.call('status')[0])
        self.assertFalse(self.call('config', {'key': 'policy.enforce', 'value': False})[0])

    def test_the_serve_builds_the_service_with_the_real_resolver(self):  # P4 O-A6
        seen = {}

        class Stop(Exception):
            pass

        def fake_build(registry, **kwargs):
            seen.update(kwargs)
            raise Stop
        with patch.object(cli, 'build', fake_build):
            with self.assertRaises(Stop):
                cli.serve(0)
        self.assertTrue(seen['with_resolver'])

    def test_build_with_the_resolver_instantiates_it_over_the_policy_config(self):  # P4 O-A6
        from brza_mcp import queue as q
        from brza_mcp.registry import ToolRegistry
        queue = q.Queue(':memory:')
        self.addCleanup(queue.close)
        service, _ = cli.build(ToolRegistry(), queue=queue, accounts=type('A', (), {'usage': lambda *a: None})(),
                               with_resolver=True)
        self.assertEqual(service.resolver.config()['resolver.tev1_model'], 'tev1:4b')


class GateProposalTest(ToolsCase):  # P4 F-A1 = O-A3
    def test_the_gate_proposal_is_the_stored_row_after_the_policy_and_carries_its_reason(self):
        _, out = self.call('dispatch', {'task': 'faz', 'role': 'executor', 'model': 'haiku', 'effort': 'max'},
                           session='s1')
        gate = out['gate']
        row = self.queue._all("SELECT * FROM requests WHERE state = 'gated'")[0]
        self.assertEqual(row['model'], 'sonnet')  # a política manda no executor
        for key in ('task', 'role', 'model', 'effort'):
            self.assertEqual(gate['proposal'][key], row[key], key)
        self.assertEqual(gate['proposal']['reason'], 'policy')

    def test_the_reply_of_dispatch_carries_model_effort_and_the_policy_reason(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'model': 'opus'}, session='s1')
        self.assertEqual((out['model'], out['effort'], out['reason']), ('sonnet', 'low', 'policy'))
        _, gate = self.call('dispatch', {'task': 't', 'role': 'executor'}, session='s1')
        _, approved = self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['gate']['id']},
                                session='s1')
        self.assertEqual((approved['model'], approved['effort']), ('sonnet', 'medium'))


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

    def test_a_refused_token_is_not_reported_as_stopped(self):  # P4 O-A9
        (Path(os.environ['BRZA_HOME']) / 'runtime' / 'mcp.token').write_text('outro-token\n')
        code, _, err = self.run_cli('policy.enforce', 'false')
        self.assertEqual(code, 1)
        self.assertIn('401', err)
        self.assertNotIn('parado', err)

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
