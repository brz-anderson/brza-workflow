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

from brza_mcp import accounts as acc  # noqa: E402
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
        'rateLimits': {**copy.deepcopy(FIXTURE['rateLimits']), 'inactiveClaudeAccounts': [inactive]}}}


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
