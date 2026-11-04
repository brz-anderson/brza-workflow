"""Testes dos gates do `dispatch` (R5.2, D3, Pedido 7) e da ferramenta `config`.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import copy
import json
from pathlib import Path
import threading
import unittest

from tools_support import ToolsCase

from brza_mcp import accounts as acc
from brza_mcp import limits as lim

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'orca-account-list.json').read_text())


class GateTest(ToolsCase):
    def gate(self, args, session='s1'):
        err, out = self.call('dispatch', args, session=session)
        self.assertFalse(err, out)
        self.assertIn('gate', out)
        return out['gate']

    def test_the_consultant_goes_straight_in(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant'}, session='s1')
        self.assertNotIn('gate', out)
        self.assertEqual(self.queue.get(out['request'])['state'], 'queued')

    def test_the_other_roles_return_a_gate_without_queueing(self):
        for role in ('executor', 'architect', 'inventado'):
            gate = self.gate({'task': f'faz {role}', 'role': role, 'model': 'opus'})
            self.assertTrue(gate['id'])
            self.assertTrue(gate['reason'])
            self.assertEqual(gate['proposal']['role'], role)
            self.assertEqual(gate['proposal']['task'], f'faz {role}')
        self.assertEqual(self.queue.pending(), [])
        self.assertEqual(len(self.queue._all("SELECT id FROM requests WHERE state = 'gated'")), 3)

    def test_the_consultant_with_account_is_gated_for_the_swap(self):
        gate = self.gate({'task': 't', 'role': 'consultant', 'account': 'pessoal'})
        self.assertEqual(gate['proposal']['account'], 'pessoal')
        self.assertIn('conta', gate['reason'])
        self.assertEqual(self.queue.pending(), [])

    def test_the_gate_ids_are_distinct(self):
        a = self.gate({'task': 'a', 'role': 'executor'})
        b = self.gate({'task': 'b', 'role': 'executor'})
        self.assertNotEqual(a['id'], b['id'])

    def test_approved_admits_the_request(self):
        gate = self.gate({'task': 't', 'role': 'executor', 'ref': 'PLN-9'})
        err, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['id']}, session='s1')
        self.assertFalse(err, out)
        self.assertEqual(out['state'], 'queued')
        self.assertEqual(out['position'], 1)
        row = self.queue.get(out['request'])
        self.assertEqual((row['state'], row['role'], row['priority']), ('queued', 'executor', 'low'))
        self.assertEqual(len(self.queue.pending()), 1)

    def test_the_gate_with_a_ref_reads_the_index_outside_the_queue_lock(self):  # P3 final A3
        seen = []
        real = self.service.index_reader

        def slow(workspace):
            seen.append(self.queue.lock._is_owned())  # o lock da fila não está com esta thread durante o `git show`
            return real(workspace)
        self.service.index_reader = slow
        self.gate({'task': 't', 'role': 'executor', 'ref': 'PLN-9'})
        self.assertEqual(seen, [False])
        self.assertEqual(self.queue._all("SELECT priority FROM requests")[0]['priority'], 'low')  # o índice valeu

    def test_the_approved_reply_carries_the_policy_reason_of_the_gate(self):  # P3 final A4, P4 final A6
        gate = self.gate({'task': 't', 'role': 'executor'})
        self.assertTrue(gate['proposal']['reason'])
        _, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['id']}, session='s1')
        self.assertEqual(out['reason'], gate['proposal']['reason'])
        self.assertEqual((out['model'], out['effort']), (gate['proposal'].get('model'), gate['proposal'].get('effort')))

    def test_an_unknown_id_is_an_error(self):
        err, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': 'gate-x'}, session='s1')
        self.assertTrue(err)
        self.assertIn('gate-x', out)
        self.assertEqual(self.queue.pending(), [])

    def test_a_reused_id_is_an_error(self):
        gate = self.gate({'task': 't', 'role': 'executor'})
        args = {'task': 't', 'role': 'executor', 'approved': gate['id']}
        self.assertFalse(self.call('dispatch', args, session='s1')[0])
        err, out = self.call('dispatch', args, session='s1')
        self.assertTrue(err)
        self.assertIn('já', out)
        self.assertEqual(len(self.queue.pending()), 1)

    def test_another_caller_cannot_approve(self):
        gate = self.gate({'task': 't', 'role': 'executor'}, session='s1')
        err, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['id']}, session='s2')
        self.assertTrue(err)
        self.assertIn('chamador', out)
        self.assertEqual(self.queue.pending(), [])
        self.assertFalse(self.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['id']},
                                   session='s1')[0])

    def test_two_simultaneous_approvals_admit_the_request_once(self):  # P3 F-A1 = O-A1
        gate = self.gate({'task': 't', 'role': 'executor'})
        barriers = {'SELECT * FROM gates': threading.Barrier(2), 'SELECT * FROM requests WHERE id': threading.Barrier(2)}
        real = self.queue._all

        def meeting(sql, args=()):
            rows = real(sql, args)
            for start, barrier in barriers.items():  # as duas leem o gate e o pedido antes de qualquer uma os gastar
                if sql.startswith(start):
                    try:
                        barrier.wait(0.5)
                    except threading.BrokenBarrierError:
                        pass
            return rows
        self.queue._all = meeting
        results = []

        def approve():
            results.append(self.call_approve(gate['id']))
        threads = [threading.Thread(target=approve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.queue.pending()), 1)
        self.assertEqual(len(self.queue._all('SELECT id FROM gates WHERE approved_at IS NOT NULL')), 1)

    def call_approve(self, gate_id):
        from brza_mcp.registry import CallContext
        out = self.service.dispatch({'task': 't', 'role': 'executor', 'approved': gate_id}, CallContext('s1'))
        return 'isError' not in out

    def test_the_gate_and_its_request_are_written_together(self):  # P3 F-A2
        real = self.queue._run

        def failing(sql, args=()):
            if sql.startswith('INSERT INTO gates'):
                raise RuntimeError('disco cheio')
            return real(sql, args)
        self.queue._run = failing
        with self.assertRaises(RuntimeError):
            self.service.dispatch({'task': 't', 'role': 'executor'}, __import__('brza_mcp.registry').registry.CallContext('s1'))
        self.assertEqual(self.queue._all("SELECT id FROM requests WHERE state = 'gated'"), [])

    def test_approval_with_another_task_role_or_account_is_refused(self):  # P3 O-A7
        gate = self.gate({'task': 'faz isso', 'role': 'executor'})
        for args in ({'task': 'faz aquilo', 'role': 'executor'}, {'task': 'faz isso', 'role': 'architect'},
                     {'task': 'faz isso', 'role': 'executor', 'account': 'outra'}):
            err, out = self.call('dispatch', {**args, 'approved': gate['id']}, session='s1')
            self.assertTrue(err, args)
            self.assertIn('proposta', out)
        self.assertEqual(self.queue.pending(), [])
        self.assertFalse(self.call('dispatch', {'task': 'faz isso', 'role': 'executor', 'approved': gate['id']},
                                   session='s1')[0])

    def test_approving_outside_claude_proposal_with_its_account_enqueues_even_with_managed_active_account(self):
        # A ativa do Claude está numa conta gerenciada (não é systemDefault)
        payload = copy.deepcopy(FIXTURE)
        payload['result']['claude']['activeAccountId'] = '8eee0e1b-f5d7-40ba-8337-8cae0df3a062'
        self.service.accounts = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(payload), ''))

        gate = self.gate({'task': 't', 'role': 'executor', 'runtime': 'grok'})
        self.assertEqual(gate['proposal']['account'], 'systemDefault')
        self.assertEqual(gate['proposal']['runtime'], 'grok')

        # Aprovar a proposta reenviando o account da proposta enfileira sem consultar a lista do Claude
        err, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'runtime': 'grok',
                                         'account': 'systemDefault', 'approved': gate['id']}, session='s1')
        self.assertFalse(err, out)
        self.assertEqual(out['state'], 'queued')
        self.assertEqual(self.queue.get(out['request'])['account'], 'systemDefault')

        # Qualquer outra conta fora da proposta é recusada com o motivo da E2
        gate2 = self.gate({'task': 't2', 'role': 'executor', 'runtime': 'grok'})
        err2, out2 = self.call('dispatch', {'task': 't2', 'role': 'executor', 'runtime': 'grok',
                                           'account': 'outra-conta', 'approved': gate2['id']}, session='s1')
        self.assertTrue(err2)
        self.assertIn('o grok tem uma conta só no app (PLN-013/R2.1)', out2)

    def _setup_codex_accounts(self, active_used=95, inactive_used=10):
        payload = copy.deepcopy(FIXTURE)
        payload['result']['codex']['accounts'] = [
            {'id': 'b', 'email': 'b@example.com'}
        ]
        payload['result']['rateLimits']['codex']['session']['usedPercent'] = active_used
        if inactive_used is not None:
            payload['result']['rateLimits']['inactiveCodexAccounts'] = [
                {'accountId': 'b', 'rateLimits': {'session': {'usedPercent': inactive_used, 'windowMinutes': 300,
                                                               'resetsAt': 1793697776000}}}
            ]
        else:
            payload['result']['rateLimits']['inactiveCodexAccounts'] = []
        accounts = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(payload), ''))
        limits = lim.Limits(self.config, usage=accounts.usage, active=accounts.active_id, machine=self.machine)
        self.service.accounts = accounts
        self.service.limits = limits
        self.limits = limits
        return accounts, payload

    def test_codex_consultant_over_cap_proposes_switch_to_inactive_account(self):
        self._setup_codex_accounts(active_used=95, inactive_used=10)
        gate = self.gate({'task': 'analisa código', 'role': 'consultant', 'runtime': 'codex'})
        self.assertEqual(gate['proposal']['account'], 'b')
        self.assertEqual(gate['proposal']['runtime'], 'codex')
        self.assertIn('troca para b', gate['reason'])
        self.assertIn('95%', gate['reason'])
        self.assertIn('10%', gate['reason'])
        self.assertEqual(self.queue.pending(), [])

    def test_codex_executor_over_cap_has_both_reasons_in_single_gate(self):
        self._setup_codex_accounts(active_used=95, inactive_used=10)
        gate = self.gate({'task': 'faz código', 'role': 'executor', 'runtime': 'codex'})
        self.assertEqual(gate['proposal']['account'], 'b')
        self.assertEqual(gate['proposal']['role'], 'executor')
        self.assertEqual(gate['proposal']['runtime'], 'codex')
        self.assertIn('não é consultant', gate['reason'])
        self.assertIn('troca para b', gate['reason'])
        self.assertIn(';', gate['reason'])
        self.assertEqual(self.queue.pending(), [])

    def test_approve_calls_select_codex_outside_lock_and_leaves_gate_open_on_error(self):
        accounts, payload = self._setup_codex_accounts(active_used=95, inactive_used=10)
        gate = self.gate({'task': 'analisa código', 'role': 'consultant', 'runtime': 'codex'})

        calls = []
        real_select = acc.select_codex

        def mock_select(account_id, client='studio', rpc=None):
            # O dublê confere que o queue.lock não está com esta thread durante a chamada
            calls.append((account_id, self.queue.lock._is_owned()))
            payload['result']['codex']['activeAccountId'] = account_id
            payload['result']['rateLimits']['codex']['session']['usedPercent'] = 10
            payload['result']['rateLimits']['inactiveCodexAccounts'] = []
            return {'kind': 'done'}

        try:
            # 1. Com o RPC falhando, o gate segue sem uso e continua aberto
            def failing_select(account_id, client='studio', rpc=None):
                raise RuntimeError('RPC caiu')

            acc.select_codex = failing_select
            err, out = self.call('dispatch', {'task': 'analisa código', 'role': 'consultant',
                                              'runtime': 'codex', 'approved': gate['id']}, session='s1')
            self.assertTrue(err)
            self.assertIn('falhou', out)
            gated_row = self.queue._all('SELECT approved_at FROM gates WHERE id = ?', (gate['id'],))
            self.assertIsNone(gated_row[0]['approved_at'])
            self.assertEqual(self.queue.pending(), [])

            # 2. Com o dublê funcionando, aprova, confere queue.lock livre e põe o pedido em queued
            acc.select_codex = mock_select
            err, out = self.call('dispatch', {'task': 'analisa código', 'role': 'consultant',
                                              'runtime': 'codex', 'approved': gate['id']}, session='s1')
            self.assertFalse(err, out)
            self.assertEqual(out['state'], 'queued')
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0], ('b', False))
            self.assertEqual(len(self.queue.pending()), 1)
            self.assertEqual(self.queue.get(out['request'])['account'], 'b')
        finally:
            acc.select_codex = real_select

    def test_codex_over_cap_without_inactive_account_enters_queue_and_waits_in_account_quota(self):
        payload = copy.deepcopy(FIXTURE)
        payload['result']['codex']['accounts'] = []
        payload['result']['rateLimits']['codex']['session']['usedPercent'] = 95
        payload['result']['rateLimits']['inactiveCodexAccounts'] = []
        accounts = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(payload), ''))
        limits = lim.Limits(self.config, usage=accounts.usage, active=accounts.active_id, machine=self.machine)
        self.service.accounts = accounts
        self.service.limits = limits
        self.limits = limits

        err, out = self.call('dispatch', {'task': 'espera cota', 'role': 'consultant', 'runtime': 'codex'}, session='s1')
        self.assertFalse(err, out)
        self.assertNotIn('gate', out)
        self.assertEqual(out['state'], 'queued')
        self.assertEqual(out['held_by'], 'account_quota')
        self.assertIn('a troca de conta do Codex só é proposta num dispatch novo (PLN-013/R3.3)', out['held_reason'])

    def test_the_gate_survives_the_service(self):
        gate = self.gate({'task': 't', 'role': 'executor'})
        from brza_mcp import tools
        from brza_mcp.telemetry import Telemetry
        from brza_mcp.registry import ToolRegistry
        other = ToolRegistry()
        tools.install(other, queue=self.queue, limits=self.limits, workspace='/ws', index_reader=lambda w: '',
                      telemetry=Telemetry(None, log=lambda m: None))
        from brza_mcp.registry import CallContext
        out = other.call('dispatch', {'task': 't', 'role': 'executor', 'approved': gate['id']}, CallContext('s1'))
        self.assertFalse(out['isError'])


class ConfigTest(ToolsCase):
    def test_write_read_and_list(self):
        err, out = self.call('config', {'key': 'orchestrator.max_workers', 'value': 3})
        self.assertFalse(err, out)
        self.assertEqual(self.call('config', {'key': 'orchestrator.max_workers'})[1]['value'], 3)
        self.call('config', {'key': 'policy.enforce', 'value': False})
        _, listing = self.call('config')
        self.assertEqual(listing['overrides'], {'orchestrator.max_workers': 3, 'policy.enforce': False})
        self.assertEqual(self.service.get_overrides(), listing['overrides'])

    def test_a_key_without_override_reads_null(self):
        self.assertIsNone(self.call('config', {'key': 'quota.hard_cap_percent'})[1]['value'])

    def test_an_unknown_key_is_refused(self):
        for key in ('policy.roles.executor.claude.model', 'mcp.port', 'foo', 'quota'):
            err, out = self.call('config', {'key': key, 'value': 1})
            self.assertTrue(err, key)
            self.assertIn(key, out)
        self.assertEqual(self.service.get_overrides(), {})

    def test_a_value_of_the_wrong_type_is_refused(self):
        self.assertTrue(self.call('config', {'key': 'policy.enforce', 'value': 'sim'})[0])
        self.assertTrue(self.call('config', {'key': 'orchestrator.max_workers', 'value': 'muitos'})[0])
        self.assertTrue(self.call('config', {'key': 'orchestrator.max_workers', 'value': True})[0])

    def test_a_mistyped_key_of_the_quota_family_is_refused(self):  # P3 O-A5
        for key in ('quota.hard_cap_percnt', 'machine.max_swapp_gb', 'resolver.tev1_modle', 'orchestrator.max_worker'):
            err, out = self.call('config', {'key': key, 'value': 1})
            self.assertTrue(err, key)
            self.assertIn(key, out)
        self.assertEqual(self.service.get_overrides(), {})

    def test_every_d4_key_is_accepted_with_its_type(self):  # P3 O-A5
        good = {'policy.enforce': True, 'policy.suggest_to_main': False, 'orchestrator.max_workers': 4,
                'quota.soft_cap_percent': 50, 'quota.hard_cap_percent': 85.5, 'quota.max_sessions_per_account': 2,
                'machine.min_free_mem_gb': 4, 'machine.max_swap_gb': 2.5, 'machine.max_cpu_percent': 80,
                'resolver.ollama_url': 'http://127.0.0.1:11434', 'resolver.tev1_model': 'tev1:4b',
                'resolver.tev1_keep_alive': '1m', 'resolver.tev1_min_confidence': 0.7,
                'resolver.haiku_max_budget_usd': 0.02}
        for key, value in good.items():
            self.assertFalse(self.call('config', {'key': key, 'value': value})[0], key)
        self.assertEqual(self.service.get_overrides(), good)

    def test_a_number_out_of_range_is_refused(self):  # P3 O-A8, P4 F-A3
        bad = [('quota.hard_cap_percent', 0), ('quota.hard_cap_percent', -1), ('quota.hard_cap_percent', 101),
               ('quota.soft_cap_percent', float('nan')), ('quota.hard_cap_percent', float('inf')),
               ('orchestrator.max_workers', -1), ('orchestrator.max_workers', 1.5),
               ('quota.max_sessions_per_account', -1), ('machine.min_free_mem_gb', -1),
               ('machine.max_swap_gb', float('nan')), ('machine.max_cpu_percent', 0),
               ('resolver.tev1_min_confidence', 1.5), ('resolver.haiku_max_budget_usd', 0),
               ('resolver.tev1_model', 3), ('resolver.ollama_url', '')]
        for key, value in bad:
            self.assertTrue(self.call('config', {'key': key, 'value': value})[0], (key, value))
        self.assertEqual(self.service.get_overrides(), {})

    def test_status_reads_the_hard_cap_like_the_limits(self):  # P3 O-A8
        self.assertEqual(self.call('status')[1]['ceilings']['quota.hard_cap_percent'], 90)
        self.call('config', {'key': 'quota.hard_cap_percent', 'value': 80})
        self.assertEqual(self.call('status')[1]['ceilings']['quota.hard_cap_percent'], 80)
        self.assertEqual(self.limits.hard_cap(), 80)

    def test_null_removes_the_override(self):
        self.call('config', {'key': 'quota.hard_cap_percent', 'value': 80})
        self.call('config', {'key': 'quota.hard_cap_percent', 'value': None})
        self.assertEqual(self.service.get_overrides(), {})

    def test_the_override_reaches_the_ceiling(self):
        self.queue.mark_running(self.queue.enqueue(task='ocupa', role='consultant'))
        self.call('config', {'key': 'orchestrator.max_workers', 'value': 1})
        _, out = self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s')
        self.assertEqual(out['held_by'], 'workspace')

    def test_it_persists_in_the_database(self):
        self.call('config', {'key': 'machine.max_swap_gb', 'value': 4.5})
        from brza_mcp import tools
        self.assertEqual(tools.get_overrides(self.queue), {'machine.max_swap_gb': 4.5})


if __name__ == '__main__':
    unittest.main()
