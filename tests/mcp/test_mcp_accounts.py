"""Testes das contas (D6, H2, H3): a fixture vira contas e instantâneos, o `CLAUDE_CONFIG_DIR` e a troca de conta.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import copy
import json
import os
import threading
import time
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
        'claude': {'accounts': [{'id': ADM, 'email': 'admin@outro-workspace.example', 'home': HOME}], 'activeAccountId': None,
                   'systemDefaultHome': '/Users/x/.claude', 'systemDefaultEmail': 'anderson@outro-workspace.example'},
        'rateLimits': {**copy.deepcopy(FIXTURE['result']['rateLimits']), 'inactiveClaudeAccounts': [inactive]}}}


class ParseTest(unittest.TestCase):
    def test_the_fixture_becomes_accounts_and_the_active_usage_snapshot(self):
        data = acc.parse(FIXTURE)
        claude = data['claude']
        self.assertEqual([a['id'] for a in claude['accounts']], ['systemDefault', '8eee0e1b-f5d7-40ba-8337-8cae0df3a062',
                                                                 'c2e4f85c-388a-4603-8edc-a0844496c902'])
        snap = claude['usage']['systemDefault']
        self.assertEqual((snap['account'], snap['runtime']), ('systemDefault', 'claude'))
        self.assertEqual([(w['name'], w['usedPercent'], w['resetsAt']) for w in snap['windows']],
                         [('session', 10, 1793701800586), ('weekly', 29, 1794114000586),
                          ('fableWeekly', 0, 1794114000000)])  # `monthly: null` fica de fora
        self.assertNotIn(ADM, claude['usage'])  # a conta sem uso fica sem instantâneo

        # Codex e Antigravity têm uso com seu runtime; Grok sem uso e com o erro dele
        codex_snap = data['codex']['usage']['systemDefault']
        self.assertEqual((codex_snap['account'], codex_snap['runtime']), ('systemDefault', 'codex'))
        self.assertEqual([(w['name'], w['usedPercent']) for w in codex_snap['windows']],
                         [('session', 77), ('weekly', 29)])

        agy_snap = data['antigravity']['usage']['systemDefault']
        self.assertEqual((agy_snap['account'], agy_snap['runtime']), ('systemDefault', 'antigravity'))
        self.assertEqual([(w['name'], w['usedPercent']) for w in agy_snap['windows']],
                         [('session', 12), ('weekly', 99)])

        self.assertEqual(data['grok']['usage'], {})
        self.assertEqual(data['grok']['error'], 'Grok did not report a usage percentage for this account')

    def test_only_the_real_shape_is_read_and_the_accounts_outside_claude_are_ignored(self):  # P2 final A2
        old = copy.deepcopy(FIXTURE)
        old['result']['accounts'] = {'claude': old['result']['claude']['accounts']}  # a forma inventada da fixture antiga
        del old['result']['claude']['accounts']
        self.assertEqual([a['id'] for a in acc.parse(old)['claude']['accounts']], ['systemDefault'])

    def test_system_default_of_codex_and_claude_do_not_mix_in_queue_samples(self):
        data = acc.parse(FIXTURE)
        queue = q.Queue(':memory:')
        limits = lim.Limits()
        acc.quota_report(data, queue, limits, now_ms=1_000_000)
        claude_samples = queue.samples('systemDefault', 'claude', 'session', 1793701800586)
        codex_samples = queue.samples('systemDefault', 'codex', 'session', 1793697776000)
        self.assertEqual(len(claude_samples), 1)
        self.assertEqual(claude_samples[0][1], 10)  # Claude a 10%
        self.assertEqual(len(codex_samples), 1)
        self.assertEqual(codex_samples[0][1], 77)  # Codex a 77%
        self.assertNotEqual(claude_samples[0][1], codex_samples[0][1])
        queue.close()

    def test_the_full_shape_gives_homes_labels_and_the_inactive_usage(self):
        data = acc.parse(full())
        default, managed = data['claude']['accounts']
        self.assertTrue(default['system_default'] and default['active'])
        self.assertEqual((managed['label'], managed['home'], managed['system_default']),
                         ('admin@outro-workspace.example', HOME, False))
        self.assertEqual(data['claude']['usage'][ADM]['windows'], [{'name': 'session', 'usedPercent': 55,
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
        default, managed = data['claude']['accounts']
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
        """O `dispatch`; o que abre gate se aprova pelo mesmo chamador, o que entra direto volta como veio."""
        _, out = self.call('dispatch', args, session='s1')
        if 'gate' not in out:
            return out
        _, ok = self.call('dispatch', {**args, 'approved': out['gate']['id']}, session='s1')
        return ok

    def test_the_account_gate_opens_only_for_a_real_swap_and_the_active_account_goes_straight_in(self):  # P3 final A2
        _, out = self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s1')  # sem account
        self.assertEqual((out['state'], self.queue.get(out['request'])['account']), ('queued', ''))
        for name in (self.EMAIL, ADM):  # o rótulo, o e-mail ou o id da própria conta ativa
            _, out = self.call('dispatch', {'task': 'b', 'role': 'consultant', 'account': name}, session='s1')
            self.assertNotIn('gate', out)
            self.assertEqual(self.queue.get(out['request'])['account'], ADM)
        err, out = self.call('dispatch', {'task': 'c', 'role': 'consultant', 'account': self.OTHER}, session='s1')
        self.assertTrue(err)  # a troca real segue recusada: o `worker-start` não tem flag de conta
        self.assertIn('conta ativa', out)
        self.assertEqual(len(self.queue._all('SELECT id FROM requests')), 3)

    def test_an_active_account_that_cannot_be_read_keeps_the_swap_gate(self):  # P3 final A2
        self.service.accounts = type('NoActive', (), {'find': lambda self, name=None: (
            {'id': 'x', 'label': 'x'} if name else None)})()
        _, out = self.call('dispatch', {'task': 'a', 'role': 'consultant', 'account': 'x'}, session='s1')
        self.assertIn('gate', out)

    def test_the_approval_normalizes_the_account_and_does_not_hold_the_queue_lock_while_reading_it(self):  # P3 final A3
        _, out = self.call('dispatch', {'task': 'a', 'role': 'executor', 'account': self.EMAIL}, session='s1')
        gate_id = out['gate']['id']
        real, started, release = self.accounts, threading.Event(), threading.Event()

        class Slow:
            def __getattr__(self, name):
                return getattr(real, name)

            def find(self, name, data=None):
                if name:
                    started.set()
                    release.wait(3)
                return real.find(name, data)
        self.service.accounts = Slow()
        done = []
        t = threading.Thread(target=lambda: done.append(self.call('dispatch', {
            'task': 'a', 'role': 'executor', 'account': self.EMAIL, 'approved': gate_id}, session='s1')))
        t.start()
        self.assertTrue(started.wait(3))
        began = time.monotonic()
        got = self.queue.lock.acquire(timeout=0.5)  # outra thread precisa do lock enquanto a leitura das contas demora
        if got:
            self.queue.lock.release()
        self.assertTrue(got)
        self.assertLess(time.monotonic() - began, 0.4)
        release.set()
        t.join(5)
        self.assertEqual(done[0][1]['state'], 'queued')

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
        self.assertEqual(self.queue.get(first['request'])['account'], '')  # P5 final ponto 1: a ativa da hora da admissão
        self.queue.mark_running(first['request'])
        _, second = self.call('dispatch', {'task': 'b', 'role': 'consultant'}, session='s1')
        self.assertEqual(second['held_by'], 'account_sessions')

    def test_a_request_without_account_follows_the_active_account_of_the_admission_time(self):  # P5 final ponto 1
        self.queue.mark_running(self.queue.enqueue(task='velho', role='consultant', account=self.OTHER, runtime='claude'))
        _, waiting = self.call('dispatch', {'task': 'novo', 'role': 'consultant'}, session='s1')
        self.assertIsNone(waiting['held_by'])  # a ativa é a ADM, e o worker aberto é da OTHER
        payload = full()
        payload['result']['claude'].update(activeAccountId=self.OTHER, accounts=[
            {'id': ADM, 'email': self.EMAIL, 'home': HOME}, {'id': self.OTHER, 'email': 'outra@example.com', 'home': '/h2'}])
        self.accounts.run = lambda argv, env, t: (0, json.dumps(payload), '')
        self.accounts.read(force=True)  # o app trocou a conta ativa antes de a vaga abrir
        self.assertIsNone(self.queue.next_admissible(self.limits))
        self.assertEqual(self.queue.get(waiting['request'])['held_by'], 'account_sessions')

    def test_the_limits_count_a_worker_opened_without_account_as_the_active_one(self):  # P2 O-A7
        self.queue.mark_running(self.queue.enqueue(task='velho', role='consultant', runtime='claude'))  # conta ''
        waiting = self.queue.enqueue(task='novo', role='consultant', runtime='claude')
        self.assertIsNone(self.queue.next_admissible(self.limits))
        self.assertEqual(self.queue.get(waiting)['held_by'], 'account_sessions')

    def test_the_approval_may_name_the_account_by_email(self):
        ok = self.approve({'task': 'a', 'role': 'consultant', 'account': self.EMAIL})
        self.assertEqual(ok['state'], 'queued')


class ClientOfTheRequestTest(unittest.TestCase):  # P5 final A5
    def setUp(self):
        self.seen = []
        studio, orca = full(), full()
        studio['result']['claude'].update(activeAccountId=None)
        orca['result']['claude'].update(activeAccountId='orca-conta', accounts=[
            {'id': 'orca-conta', 'email': 'orca@example.com', 'home': '/o'}])
        orca['result']['rateLimits'] = {'claude': {'session': {'usedPercent': 77, 'windowMinutes': 300,
                                                               'resetsAt': 2_000_000_000_000}}}

        def run(argv, env, timeout):
            self.seen.append(env['ORCA_USER_DATA_PATH'])
            return 0, json.dumps(orca if env['ORCA_USER_DATA_PATH'].endswith('/orca') else studio), ''
        self.accounts = acc.Accounts(run=run, detect=lambda: 'studio')  # os dois apps de pé: o detect dá o Studio

    def test_the_active_account_and_the_usage_come_from_the_client_of_the_request(self):
        self.assertEqual(self.accounts.active_id(), 'systemDefault')
        self.assertEqual(self.accounts.active_id(client='orca'), 'orca-conta')
        self.assertEqual(self.accounts.usage('orca-conta', 'claude', client='orca')['windows'][0]['usedPercent'], 77)
        self.assertIsNone(self.accounts.usage('orca-conta', 'claude'))  # o Studio não conhece a conta do Orca

    def test_the_limits_judge_a_request_of_the_orca_with_the_accounts_and_the_usage_of_the_orca(self):
        limits = lim.Limits({'quota.hard_cap_percent': 70}, usage=self.accounts.usage, machine=lambda: {},
                            active=self.accounts.active_id)
        queue = q.Queue(':memory:')
        studio = queue.enqueue(task='a', role='consultant', runtime='claude', client='studio')
        orca = queue.enqueue(task='b', role='consultant', runtime='claude', client='orca')
        self.assertEqual(queue.next_admissible(limits)['id'], studio)  # a ativa do Studio está em 10%
        queue.mark_running(studio)
        self.assertIsNone(queue.next_admissible(limits))  # a do Orca está em 77%, acima do teto de 70
        self.assertEqual(queue.get(orca)['held_by'], 'account_quota')
        self.assertEqual(limits.last_measures['account']['account'], 'orca-conta')

    def test_the_cache_is_per_client_and_a_failure_is_not_repeated_within_the_ttl(self):  # P5 final A6
        now, calls = [0.0], []

        def run(argv, env, timeout):
            calls.append(env['ORCA_USER_DATA_PATH'])
            return 1, '', 'app travado'
        accounts = acc.Accounts(run=run, detect=lambda: 'studio', clock=lambda: now[0])
        for _ in range(3):
            with self.assertRaises(acc.AccountError):
                accounts.read()
        self.assertEqual(len(calls), 1)  # a falha também fica em cache pelo TTL
        with self.assertRaises(acc.AccountError):
            accounts.read(client='orca')
        self.assertEqual(len(calls), 2)
        now[0] = 31.0
        with self.assertRaises(acc.AccountError):
            accounts.read()
        self.assertEqual(len(calls), 3)
        accounts.warm()  # o `warm` não levanta
        self.assertEqual(len(calls), 3)


class WarmBeforeTheLockTest(ToolsCase):  # P5 final A6
    def test_status_and_queued_warm_the_accounts_before_taking_the_queue_lock(self):
        started, release = threading.Event(), threading.Event()
        holders = []

        class Slow:
            def warm(self, client=None):
                holders.append(self_.queue.lock._is_owned())
                started.set()
                release.wait(3)

            def find(self, name, data=None, client=None):
                return None

            def active_id(self, client=None):
                return None
        self_ = self
        self.service.accounts = Slow()
        t = threading.Thread(target=lambda: self.call('status', session='s1'))
        t.start()
        self.assertTrue(started.wait(3))
        got = self.queue.lock.acquire(timeout=0.5)  # a leitura lenta das contas não segura a fila
        if got:
            self.queue.lock.release()
        self.assertTrue(got)
        release.set()
        t.join(5)
        self.assertEqual(holders, [False])

    def test_the_scheduler_prepare_warms_the_accounts_too(self):
        warmed = []
        self.service.accounts = type('W', (), {'warm': lambda self, client=None: warmed.append(client)})()
        self.service.prepare()
        self.assertEqual(warmed, [None])


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

    def test_the_finished_follows_the_same_caller_as_the_dispatch_header_stripped_or_the_argument(self):  # P3 A5, P5 A7
        a = self.queue.enqueue(task='a', role='consultant', caller='arg-1')
        b = self.queue.enqueue(task='b', role='consultant', caller='s1')
        for rid in (a, b):
            self.queue.mark_running(rid)
            self.queue.mark_done(rid)
        self.assertEqual([r['task'] for r in self.call('status', {'caller': 'arg-1'})[1]['finished']], ['a'])
        self.assertEqual([r['task'] for r in self.call('status', session=' s1 ')[1]['finished']], ['b'])
        # o header vence o argumento, como no `dispatch`
        self.assertEqual([r['task'] for r in self.call('status', {'caller': 'arg-1'}, session='s1')[1]['finished']], ['b'])
        self.assertEqual(self.call('status', {'caller': '  '})[1]['finished'], [])

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


class SelectCodexTest(unittest.TestCase):
    def test_select_codex_sends_none_for_system_default(self):
        calls = []

        def rpc(method, params=None, timeout=10.0):
            calls.append((method, params, os.environ.get('ORCA_USER_DATA_PATH')))
            return {'kind': 'done'}

        acc.select_codex('systemDefault', client='studio', rpc=rpc)
        self.assertEqual(len(calls), 1)
        method, params, env = calls[0]
        self.assertEqual(method, 'accounts.selectCodex')
        self.assertIsNone(params['accountId'])
        self.assertTrue(env.endswith('brza-studio'))

    def test_select_codex_sends_account_id_for_managed_account(self):
        calls = []

        def rpc(method, params=None, timeout=10.0):
            calls.append((method, params, os.environ.get('ORCA_USER_DATA_PATH')))
            return {'kind': 'done'}

        acc.select_codex('codex-acc-2', client='studio', rpc=rpc)
        self.assertEqual(len(calls), 1)
        method, params, env = calls[0]
        self.assertEqual(method, 'accounts.selectCodex')
        self.assertEqual(params['accountId'], 'codex-acc-2')

    def test_select_codex_raises_account_error_on_rpc_failure(self):
        def rpc(method, params=None, timeout=10.0):
            raise RuntimeError('conexão recusada')

        with self.assertRaises(acc.AccountError) as cm:
            acc.select_codex('codex-acc-2', client='studio', rpc=rpc)
        self.assertIn('falhou', str(cm.exception))

    def test_select_codex_raises_account_error_when_app_refuses(self):
        def rpc(method, params=None, timeout=10.0):
            return {'kind': 'not-done', 'notice': 'permissão negada'}

        with self.assertRaisesRegex(acc.AccountError, 'permissão negada'):
            acc.select_codex('codex-acc-2', client='studio', rpc=rpc)


class ProfileAdmissionTest(unittest.TestCase):
    def test_profile_accounts_and_usage_have_separate_caches_and_drive_admission(self):
        calls = []
        def run(argv, env, timeout):
            profile = env['ORCA_USER_DATA_PATH']
            calls.append(profile)
            data = copy.deepcopy(FIXTURE)
            data['result']['claude']['activeAccountId'] = 'dev-A' if profile == '/tmp/dev-A' else 'dev-B'
            data['result']['claude']['accounts'] = [
                {'id': 'dev-A', 'email': 'a@example.com', 'home': '/a'},
                {'id': 'dev-B', 'email': 'b@example.com', 'home': '/b'}]
            data['result']['rateLimits']['claude']['session']['usedPercent'] = 10 if profile == '/tmp/dev-A' else 99
            return 0, json.dumps(data), ''
        accounts = acc.Accounts(client='studio', run=run)
        queue = q.Queue(':memory:')
        limits = lim.Limits({'quota.max_sessions_per_account': 1}, active=accounts.active_id,
                            usage=accounts.usage, machine=lambda: {})
        first = queue.enqueue(task='a', role='consultant', runtime='claude', client='studio', user_data='/tmp/dev-A')
        second = queue.enqueue(task='b', role='consultant', runtime='claude', client='studio', user_data='/tmp/dev-B')
        self.assertEqual(queue.admit_next(limits)['id'], first)
        self.assertEqual(queue.get(second)['held_by'], 'account_quota')
        self.assertEqual(queue.running_count('dev-A', 'claude'), 1)
        self.assertEqual(accounts.active_id(client='studio', user_data='/tmp/dev-B'), 'dev-B')
        self.assertEqual(calls, ['/tmp/dev-A', '/tmp/dev-B'])
        queue.close()


if __name__ == '__main__':
    unittest.main()
