"""Testes das contas (D6, H2, H3): a fixture vira contas e instantâneos, o `CLAUDE_CONFIG_DIR` e a troca de conta.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_support import paths  # noqa: F401
from tools_support import ToolsCase

from brza_mcp import accounts as acc  # noqa: E402
from brza_mcp import clients  # noqa: E402
from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'orca-account-list.json').read_text())
ADM = '8eee0e1b-f5d7-40ba-8337-8cae0df3a062'
HOME = '/Users/x/Library/Application Support/brza-studio/claude-profiles/8eee0e1b/home'


def full():
    """A forma completa do `account list`: contas em `result.claude.accounts`, o `systemDefaultHome` e o uso dos inativos."""
    inactive = {'accountId': ADM, 'updatedAt': 1, 'isFetching': False, 'rateLimits': {
        'session': {'usedPercent': 55, 'windowMinutes': 300, 'resetsAt': 1793701800586},
        'weekly': None}}
    return {'ok': True, 'result': {
        'claude': {'accounts': [{'id': ADM, 'email': 'admin@fixfy.com.br', 'home': HOME}], 'activeAccountId': None,
                   'systemDefaultHome': '/Users/x/.claude', 'systemDefaultEmail': 'anderson@fixfy.com.br'},
        'rateLimits': {**copy.deepcopy(FIXTURE['result']['rateLimits']), 'inactiveClaudeAccounts': [inactive]}}}


class ParseTest(unittest.TestCase):
    def test_the_fixture_becomes_accounts_and_the_active_usage_snapshot(self):
        data = acc.parse(FIXTURE)
        self.assertEqual([a['id'] for a in data['accounts']], ['systemDefault', '8eee0e1b-f5d7-40ba-8337-8cae0df3a062',
                                                              'c2e4f85c-388a-4603-8edc-a0844496c902'])
        snap = data['usage']['systemDefault']
        self.assertEqual((snap['account'], snap['runtime']), ('systemDefault', 'claude'))
        self.assertEqual([(w['name'], w['usedPercent'], w['resetsAt']) for w in snap['windows']],
                         [('session', 10, 1793701800586), ('weekly', 29, 1794114000586),
                          ('fableWeekly', 0, 1794114000000)])  # `monthly: null` fica de fora
        self.assertNotIn(ADM, data['usage'])  # a conta sem uso fica sem instantâneo

    def test_the_full_shape_gives_homes_labels_and_the_inactive_usage(self):
        data = acc.parse(full())
        default, managed = data['accounts']
        self.assertTrue(default['system_default'] and default['active'])
        self.assertEqual((managed['label'], managed['home'], managed['system_default']),
                         ('admin@fixfy.com.br', HOME, False))
        self.assertEqual(data['usage'][ADM]['windows'], [{'name': 'session', 'usedPercent': 55,
                                                          'resetsAt': 1793701800586, 'windowMinutes': 300}])

    def test_the_provider_feeds_the_limits_and_the_account_without_usage_gets_r2_5(self):
        accounts = acc.Accounts(client='studio', run=lambda argv, env, t: (0, json.dumps(FIXTURE), ''))
        self.assertEqual(accounts.usage('systemDefault', 'claude')['windows'][0]['usedPercent'], 10)
        self.assertEqual(accounts.usage('', 'claude')['account'], 'systemDefault')  # sem conta: a ativa
        self.assertIsNone(accounts.usage(ADM, 'claude'))
        queue = q.Queue(':memory:')
        limits = lim.Limits({'quota.hard_cap_percent': 90}, usage=accounts.usage, machine=lambda: {})
        queue.enqueue(task='t', role='consultant', account=ADM, runtime='claude')
        self.assertEqual(queue.next_admissible(limits)['account'], ADM)
        self.assertFalse(limits.last_measures['quota']['known'])

    def test_the_account_list_runs_with_the_client_env(self):
        seen = {}

        def run(argv, env, timeout):
            seen.update(argv=argv, env=env)
            return 0, json.dumps(FIXTURE), ''
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'h'}):
            acc.Accounts(client='studio', run=run).read()
        self.assertEqual(seen['argv'][1:], ['account', 'list', '--json'])
        self.assertTrue(seen['env']['ORCA_USER_DATA_PATH'].endswith('brza-studio'))
        self.assertNotIn('ORCA_TERMINAL_HANDLE', seen['env'])

    def test_a_failure_of_the_cli_is_an_account_error_and_the_provider_answers_none(self):
        accounts = acc.Accounts(client='studio', run=lambda argv, env, t: (1, '', 'boom'))
        with self.assertRaises(acc.AccountError):
            accounts.read()
        self.assertIsNone(accounts.usage('systemDefault', 'claude'))


class ClaudeEnvTest(unittest.TestCase):
    def test_the_managed_account_gets_the_config_dir_and_the_system_default_does_not(self):
        data = acc.parse(full())
        default, managed = data['accounts']
        base = {'PATH': '/usr/bin', 'CLAUDE_CONFIG_DIR': '/herdado'}
        self.assertEqual(acc.claude_env(managed, base), {'PATH': '/usr/bin', 'CLAUDE_CONFIG_DIR': HOME})
        self.assertEqual(acc.claude_env(default, base), {'PATH': '/usr/bin'})  # nem o herdado
        self.assertEqual(acc.claude_env(None, base), {'PATH': '/usr/bin'})


class UsageByAccountTest(unittest.TestCase):
    def test_each_account_gets_its_own_usage_and_the_limits_project_it(self):  # P2 O-A6
        accounts = acc.Accounts(client='studio', run=lambda argv, env, t: (0, json.dumps(full()), ''))
        self.assertEqual(accounts.usage(ADM, 'claude')['windows'][0]['usedPercent'], 55)  # a inativa, pelo seu bloco
        self.assertEqual(accounts.usage('systemDefault', 'claude')['windows'][0]['usedPercent'], 10)  # a ativa
        limits = lim.Limits({'quota.hard_cap_percent': 50}, usage=accounts.usage, machine=lambda: {})
        queue = q.Queue(':memory:')
        queue.enqueue(task='t', role='consultant', account=ADM, runtime='claude')
        self.assertIsNone(queue.next_admissible(limits))  # 55% >= 50%: o teto vale para a inativa
        self.assertEqual(queue.pending()[0]['held_by'], 'account_quota')


class ClientBinaryTest(unittest.TestCase):
    def test_the_orca_cli_is_an_absolute_path_that_does_not_depend_on_the_agent_path(self):  # P5 F-A2 = O-A2
        self.assertTrue(clients.CLIENTS['orca']['cli'].startswith('/'))
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            self.assertTrue(clients.cli_path('orca').startswith('/'))

    def test_without_the_app_the_orca_binary_comes_from_the_path_and_the_studio_has_no_fallback(self):
        with patch.dict(clients.CLIENTS['orca'], {'cli': '/nao/existe/orca'}), \
                patch('brza_mcp.clients.shutil.which', return_value='/opt/x/orca') as which:
            self.assertEqual(clients.cli_path('orca'), '/opt/x/orca')
        which.assert_called_once_with('orca')
        with patch.dict(clients.CLIENTS['studio'], {'cli': '/nao/existe/brza'}), \
                patch('brza_mcp.clients.shutil.which', return_value='/usr/local/bin/brza'):
            self.assertEqual(clients.cli_path('studio'), '/nao/existe/brza')  # `brza` no PATH é o CLI do workflow

    def test_the_account_list_uses_the_resolved_binary(self):
        seen = {}
        with patch.dict(clients.CLIENTS['orca'], {'cli': '/nao/existe/orca'}), \
                patch('brza_mcp.clients.shutil.which', return_value='/opt/x/orca'):
            acc.Accounts(client='orca', run=lambda argv, env, t: seen.update(argv=argv) or (0, json.dumps(FIXTURE), '')).read()
        self.assertEqual(seen['argv'][0], '/opt/x/orca')


class AccountRulesTest(ToolsCase):
    """O `dispatch` com o leitor de contas: a mesma conta sob chaves diferentes e a recusa da que não é a ativa."""
    OTHER = 'c2e4f85c-388a-4603-8edc-a0844496c902'
    EMAIL = 'admin@example.com'
    config = {'quota.max_sessions_per_account': 1}

    def setUp(self):
        super().setUp()
        payload = full()
        payload['result']['claude']['activeAccountId'] = ADM
        payload['result']['claude']['accounts'] = [
            {'id': ADM, 'email': self.EMAIL, 'home': HOME}, {'id': self.OTHER, 'email': 'outra@example.com', 'home': '/h2'}]
        self.accounts = acc.Accounts(client='studio', run=lambda argv, env, t: (0, json.dumps(payload), ''))
        self.service.accounts = self.accounts
        self.limits.active = self.accounts.active_id

    def approve(self, args):
        _, out = self.call('dispatch', args, session='s1')
        self.assertIn('gate', out)
        _, ok = self.call('dispatch', {**args, 'approved': out['gate']['id']}, session='s1')
        return ok

    def test_an_account_that_is_not_the_active_one_is_refused_with_the_reason(self):  # P5 O-A1
        err, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'account': self.OTHER}, session='s1')
        self.assertTrue(err)
        self.assertIn('conta ativa', out)
        self.assertIn('worker-start', out)
        err, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'account': 'nao-existe'}, session='s1')
        self.assertTrue(err)
        self.assertIn('desconhecida', out)
        self.assertEqual(self.queue._all('SELECT id FROM requests'), [])

    def test_the_active_account_by_id_and_by_email_is_the_same_account_under_the_ceiling(self):  # P5 O-A4
        first = self.approve({'task': 'a', 'role': 'consultant', 'account': self.EMAIL})
        self.assertEqual(self.queue.get(first['request'])['account'], ADM)
        self.queue.mark_running(first['request'])
        second = self.approve({'task': 'b', 'role': 'consultant', 'account': ADM})
        self.assertEqual(second['held_by'], 'account_sessions')

    def test_a_request_without_account_counts_in_the_active_account(self):  # P5 O-A4 / P2 O-A7
        _, first = self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s1')
        self.assertEqual(self.queue.get(first['request'])['account'], ADM)
        self.queue.mark_running(first['request'])
        _, second = self.call('dispatch', {'task': 'b', 'role': 'consultant'}, session='s1')
        self.assertEqual(second['held_by'], 'account_sessions')

    def test_the_limits_count_a_worker_opened_without_account_as_the_active_one(self):  # P2 O-A7
        self.queue.mark_running(self.queue.enqueue(task='velho', role='consultant', runtime='claude'))  # conta ''
        waiting = self.queue.enqueue(task='novo', role='consultant', runtime='claude')
        self.assertIsNone(self.queue.next_admissible(self.limits))
        self.assertEqual(self.queue.get(waiting)['held_by'], 'account_sessions')

    def test_the_approval_may_name_the_account_by_email(self):
        ok = self.approve({'task': 'a', 'role': 'consultant', 'account': self.EMAIL})
        self.assertEqual(ok['state'], 'queued')


class StatusFinishedTest(ToolsCase):
    def test_status_returns_the_recent_finished_and_failed_of_the_caller_with_the_error(self):  # P5 O-A3
        done = self.queue.enqueue(task='a', role='consultant', caller='s1')
        failed = self.queue.enqueue(task='b', role='consultant', caller='s1')
        other = self.queue.enqueue(task='c', role='consultant', caller='s2')
        for rid in (done, failed, other):
            self.queue.mark_running(rid)
        self.queue.mark_done(done)
        self.queue.mark_done(failed, failed=True, error='orchestration worker-start falhou: boom')
        self.queue.mark_done(other)
        _, st = self.call('status', session='s1')
        got = {r['task']: (r['state'], r.get('error')) for r in st['finished']}
        self.assertEqual(got, {'a': ('done', None), 'b': ('failed', 'orchestration worker-start falhou: boom')})
        self.assertEqual(self.call('status')[1]['finished'], [])

    def test_a_queued_request_that_cannot_open_shows_why(self):  # P5 F-A5
        rid = self.queue.enqueue(task='a', role='consultant', caller='s1')
        self.queue.set_error(rid, 'cliente desconhecido')
        _, st = self.call('status', session='s1')
        self.assertEqual(st['queued'][0]['error'], 'cliente desconhecido')


class SwitchTest(unittest.TestCase):
    def switch(self, handler, **kw):
        calls = []

        def rpc(method, params=None, timeout=10.0):
            calls.append((method, params, os.environ.get('ORCA_USER_DATA_PATH')))
            return handler(method, params)
        return calls, lambda: acc.switch_chat_account('sess-1', ADM, rpc=rpc, **kw)

    def test_the_switch_calls_the_rpc_with_the_envelope_at_the_session_fence(self):
        calls, go = self.switch(lambda m, p: {'page': {'fence': 4}} if m == 'agentSession.history'
                                else {'kind': 'done'})
        self.assertEqual(go(), {'kind': 'done'})
        (hist, _, data), (method, params, _) = calls[0], calls[1]
        self.assertTrue(data.endswith('brza-studio'))
        self.assertEqual(method, 'agentSession.switchAccount')
        self.assertEqual(params['accountId'], ADM)
        self.assertEqual((params['envelope']['sessionId'], params['envelope']['expectedRuntimeFence']), ('sess-1', 4))
        self.assertEqual(len(params['envelope']['payloadFingerprint']), 64)

    def test_the_system_default_goes_as_null(self):
        calls = []
        acc.switch_chat_account('s', 'systemDefault', rpc=lambda m, p=None, **k: calls.append(p) or {'page': {'fence': 1}})
        self.assertIsNone(calls[1]['accountId'])

    def test_an_active_turn_is_a_clear_error_without_retry(self):
        def handler(method, params):
            if method == 'agentSession.history':
                return {'page': {'fence': 1}}
            raise RuntimeError('agentSession.switchAccount: {"code": "agent_session_operation_invalid", '
                               '"details": {"reason": "turnActive"}}')
        calls, go = self.switch(handler)
        with self.assertRaises(acc.AccountError) as cm:
            go()
        self.assertIn('turno ativo', str(cm.exception))
        self.assertEqual(sum(1 for c in calls if c[0] == 'agentSession.switchAccount'), 1)

    def test_an_operation_invalid_without_the_turn_active_reason_is_not_an_active_turn(self):  # P5 O-A10
        def handler(method, params):
            if method == 'agentSession.history':
                return {'page': {'fence': 1}}
            raise RuntimeError('agentSession.switchAccount: {"code": "agent_session_operation_invalid", '
                               '"details": {"reason": "sessionClosed"}}')
        _, go = self.switch(handler)
        with self.assertRaises(acc.AccountError) as cm:
            go()
        self.assertNotIn('turno ativo', str(cm.exception))
        self.assertIn('sessionClosed', str(cm.exception))

    def test_a_refusal_of_the_app_is_an_error(self):
        _, go = self.switch(lambda m, p: {'page': {'fence': 1}} if m == 'agentSession.history'
                            else {'kind': 'not-done', 'notice': 'sem login'})
        with self.assertRaisesRegex(acc.AccountError, 'sem login'):
            go()

    def test_the_process_env_is_restored(self):
        before = os.environ.get('ORCA_USER_DATA_PATH')
        _, go = self.switch(lambda m, p: {'page': {'fence': 1}, 'kind': 'done'})
        go()
        self.assertEqual(os.environ.get('ORCA_USER_DATA_PATH'), before)


if __name__ == '__main__':
    unittest.main()
