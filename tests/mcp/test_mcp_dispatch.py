"""Testes do `dispatch`, do `status` e do schema das ferramentas, pelo servidor de verdade (D3, Pedido 2).

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools_support import ToolsCase

from brza_mcp import accounts as acc
from brza_mcp import limits as lim

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'orca-account-list.json').read_text())


class SchemaTest(ToolsCase):
    def test_the_three_tools_are_listed_with_the_d3_schema(self):
        _, _, msg = self.rpc('tools/list')
        tools = {t['name']: t['inputSchema'] for t in msg['result']['tools']}
        self.assertEqual(set(tools), {'dispatch', 'status', 'config'})
        self.assertEqual(tools['dispatch']['required'], ['task', 'role'])
        props = tools['dispatch']['properties']
        self.assertEqual(props['runtime'], {'enum': ['claude', 'codex', 'grok', 'antigravity', 'agy']})
        self.assertEqual(props['priority'], {'enum': ['high', 'medium', 'low']})
        self.assertEqual(props['blocks'], {'type': 'array', 'items': {'type': 'string'}})
        self.assertEqual(set(props), {'task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref',
                                      'deadline', 'blocks', 'priority', 'caller', 'approved'})


class CallerTest(ToolsCase):
    def test_the_session_header_becomes_the_caller(self):
        err, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='sess-1')
        self.assertFalse(err)
        self.assertEqual(out['state'], 'queued')
        self.assertEqual(self.queue.get(out['request'])['caller'], 'sess-1')

    def test_without_the_header_the_argument_is_used(self):
        err, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant', 'caller': 'arg-1'})
        self.assertFalse(err)
        self.assertEqual(self.queue.get(out['request'])['caller'], 'arg-1')

    def test_the_header_wins_over_the_argument(self):
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant', 'caller': 'arg-1'}, session='sess-1')
        self.assertEqual(self.queue.get(out['request'])['caller'], 'sess-1')

    def test_without_either_it_is_an_error(self):
        err, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'})
        self.assertTrue(err)
        self.assertIn('X-Brza-Session', out)
        self.assertEqual(self.queue.pending(), [])


class CallerEdgeTest(ToolsCase):  # P3 F-A4, O-A12
    def test_an_empty_header_falls_back_to_the_argument(self):
        err, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant', 'caller': 'arg-1'}, session='')
        self.assertFalse(err, out)
        self.assertEqual(self.queue.get(out['request'])['caller'], 'arg-1')

    def test_a_blank_caller_is_refused(self):
        for session, args in (('', {}), ('   ', {}), (None, {'caller': '   '}), ('   ', {'caller': ' \t'})):
            err, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant', **args}, session=session)
            self.assertTrue(err, (session, args))
            self.assertIn('chamador', out)
        self.assertEqual(self.queue.pending(), [])

    def test_the_caller_is_stored_without_the_spaces(self):
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant', 'caller': ' arg-1 '})
        self.assertEqual(self.queue.get(out['request'])['caller'], 'arg-1')


class RuntimeDefaultTest(ToolsCase):  # P3 O-A2, P4 O-A4
    config = {'quota.max_sessions_per_account': 1}

    def test_a_request_without_runtime_counts_in_the_same_session_ceiling_as_claude(self):
        _, first = self.call('dispatch', {'task': 'a', 'role': 'consultant', 'runtime': 'claude'}, session='s')
        self.assertEqual(self.queue.get(first['request'])['runtime'], 'claude')
        self.queue.mark_running(first['request'])
        _, second = self.call('dispatch', {'task': 'b', 'role': 'consultant'}, session='s')
        self.assertEqual(self.queue.get(second['request'])['runtime'], 'claude')
        self.assertEqual(second['held_by'], 'account_sessions')


class ValidationTest(ToolsCase):
    def refused(self, args, fragment):
        err, out = self.call('dispatch', args, session='s')
        self.assertTrue(err, out)
        self.assertIn(fragment, out)
        self.assertEqual(self.queue.pending(), [])

    def test_each_runtime_is_accepted_and_agy_is_stored_as_antigravity(self):
        for r in ('grok', 'codex', 'antigravity', 'agy'):
            err, out = self.call('dispatch', {'task': f't-{r}', 'role': 'consultant', 'runtime': r}, session='s')
            self.assertFalse(err, out)
            row = self.queue.get(out['request'])
            expected = 'antigravity' if r == 'agy' else r
            self.assertEqual(row['runtime'], expected)

    def test_account_outside_claude_respects_single_account_or_system_default(self):
        self.refused({'task': 't', 'role': 'consultant', 'runtime': 'grok', 'account': 'x'},
                     'o grok tem uma conta só no app (PLN-013/R2.1)')
        err, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'runtime': 'grok', 'account': 'systemDefault'}, session='s')
        self.assertFalse(err, out)
        self.assertEqual(self.queue.get(out['request'])['account'], 'systemDefault')

    def test_the_account_stored_outside_claude_is_the_runtime_default(self):
        for r in ('grok', 'antigravity'):
            _, out = self.call('dispatch', {'task': f't-{r}', 'role': 'consultant', 'runtime': r}, session='s')
            self.assertEqual(self.queue.get(out['request'])['account'], 'systemDefault')

        # Codex com a fixture real com codex.activeAccountId: null lida por parse e active_id
        fixture_null = copy.deepcopy(FIXTURE)
        acc_null = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(fixture_null), ''))
        self.service.accounts = acc_null
        _, out = self.call('dispatch', {'task': 't-codex', 'role': 'consultant', 'runtime': 'codex'}, session='s')
        self.assertEqual(self.queue.get(out['request'])['account'], 'systemDefault')

        # Codex com codex.activeAccountId preenchido na fixture
        fixture_filled = copy.deepcopy(FIXTURE)
        fixture_filled['result']['codex']['activeAccountId'] = 'codex-custom'
        acc_filled = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(fixture_filled), ''))
        self.service.accounts = acc_filled
        _, out = self.call('dispatch', {'task': 't-codex-2', 'role': 'consultant', 'runtime': 'codex'}, session='s')
        self.assertEqual(self.queue.get(out['request'])['account'], 'codex-custom')

    def test_effort_without_model_outside_claude_drops_effort_from_queue_and_proposal(self):
        # papel sem linha fora do Claude, com effort e sem model: o esforço anulado sai da fila e da proposta
        _, out = self.call('dispatch', {'task': 't', 'role': 'mistério', 'runtime': 'grok', 'effort': 'low'}, session='s')
        self.assertIn('gate', out)
        proposal = out['gate']['proposal']
        self.assertNotIn('effort', proposal)
        self.assertNotIn('model', proposal)
        gate_request = self.queue._all('SELECT request_id FROM gates WHERE id = ?', (out['gate']['id'],))[0]['request_id']
        row = self.queue.get(gate_request)
        self.assertIsNone(row['effort'])
        self.assertIsNone(row['model'])

        err, approved = self.call('dispatch', {'task': 't', 'role': 'mistério', 'runtime': 'grok',
                                              'approved': out['gate']['id']}, session='s')
        self.assertFalse(err, approved)
        self.assertEqual(approved['state'], 'queued')
        self.assertIsNone(approved['effort'])
        self.assertIsNone(approved['model'])
        row_approved = self.queue.get(approved['request'])
        self.assertIsNone(row_approved['effort'])
        self.assertIsNone(row_approved['model'])

    def test_gate_proposal_of_executor_grok_carries_runtime_grok(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'executor', 'runtime': 'grok'}, session='s')
        self.assertIn('gate', out)
        self.assertEqual(out['gate']['proposal']['runtime'], 'grok')

    def test_claude_is_accepted(self):
        err, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'runtime': 'claude'}, session='s')
        self.assertFalse(err, out)

    def test_missing_required(self):
        self.refused({'role': 'consultant'}, 'task')
        self.refused({'task': 't'}, 'role')

    def test_wrong_types(self):
        self.refused({'task': 5, 'role': 'consultant'}, 'task')
        self.refused({'task': 't', 'role': 'consultant', 'blocks': 'PLN-1'}, 'blocks')
        self.refused({'task': 't', 'role': 'consultant', 'blocks': [1]}, 'blocks')
        self.refused({'task': 't', 'role': 'consultant', 'model': ['x']}, 'model')

    def test_wrong_enums(self):
        self.refused({'task': 't', 'role': 'consultant', 'priority': 'urgent'}, 'priority')

    def test_wrong_date(self):
        self.refused({'task': 't', 'role': 'consultant', 'deadline': 'amanhã'}, 'deadline')

    def test_the_deadline_outside_the_iso_form_is_refused(self):  # P3 O-A4
        for bad in ('20261105', '2026-W45-4', '2026-11-5'):
            self.refused({'task': 't', 'role': 'consultant', 'deadline': bad}, 'deadline')

    def test_unknown_argument(self):
        self.refused({'task': 't', 'role': 'consultant', 'acount': 'x'}, 'acount')

    def test_the_server_survives_the_errors(self):
        self.call('dispatch', {'task': 5}, session='s')
        err, _ = self.call('dispatch', {'task': 't', 'role': 'consultant'}, session='s')
        self.assertFalse(err)


class IndexTest(ToolsCase):
    def test_the_index_fills_what_is_missing_from_the_ref(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'ref': 'PLN-9'}, session='s')
        row = self.queue.get(out['request'])
        self.assertEqual((row['priority'], row['blocks'], row['deadline']), ('low', ['PLN-3'], '2026-12-01'))

    def test_the_argument_wins_over_the_index(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant', 'ref': 'PLN-9', 'priority': 'high'},
                           session='s')
        self.assertEqual(self.queue.get(out['request'])['priority'], 'high')

    def test_without_ref_nothing_is_filled(self):
        _, out = self.call('dispatch', {'task': 't', 'role': 'consultant'}, session='s')
        self.assertIsNone(self.queue.get(out['request'])['priority'])


class OrderAndStatusTest(ToolsCase):
    config = {'orchestrator.max_workers': 1}

    def test_the_reply_has_position_and_the_ceiling(self):
        self.queue.mark_running(self.queue.enqueue(task='ocupa', role='consultant'))
        _, first = self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s')
        _, second = self.call('dispatch', {'task': 'b', 'role': 'consultant', 'priority': 'high'}, session='s')
        self.assertEqual(first['state'], 'queued')
        self.assertEqual(first['held_by'], 'workspace')
        self.assertEqual(first['position'], 1)
        self.assertEqual(second['position'], 1)  # o priority high passa à frente

    def test_the_reply_without_ceiling(self):
        _, out = self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s')
        self.assertIsNone(out['held_by'])
        self.assertEqual(out['position'], 1)

    def test_status_shows_the_ceiling_that_holds(self):
        self.queue.mark_running(self.queue.enqueue(task='ocupa', role='consultant'))
        self.call('dispatch', {'task': 'a', 'role': 'consultant'}, session='s')
        self.call('dispatch', {'task': 'g', 'role': 'executor'}, session='s')
        err, st = self.call('status')
        self.assertFalse(err)
        self.assertEqual([r['task'] for r in st['queued']], ['a'])
        self.assertEqual(st['queued'][0]['held_by'], 'workspace')
        self.assertIn('orchestrator.max_workers', st['queued'][0]['held_reason'])
        self.assertEqual([r['task'] for r in st['gated']], ['g'])
        self.assertEqual([r['task'] for r in st['running']], ['ocupa'])
        self.assertEqual(st['ceilings']['orchestrator.max_workers'], 1)

    def test_status_returns_quota_unknown_with_grok_error(self):
        accounts = acc.Accounts(client='orca', run=lambda argv, env, t: (0, json.dumps(FIXTURE), ''))
        self.service.accounts = accounts
        self.service.limits = lim.Limits(active=accounts.active_id, usage=accounts.usage,
                                         usage_error=accounts.usage_error)
        err, out = self.call('status', session='s')
        self.assertFalse(err, out)
        self.assertIn('quota_unknown', out['ceilings'])
        self.assertEqual(out['ceilings']['quota_unknown'].get('grok'),
                         'Grok did not report a usage percentage for this account')

    def test_status_gives_the_gate_id_only_to_the_caller_that_opened_it(self):  # P3 F-A3, final A1
        _, gate = self.call('dispatch', {'task': 'g', 'role': 'executor'}, session='s')
        _, st = self.call('status', session='s')
        self.assertEqual(st['gated'][0]['gate_id'], gate['gate']['id'])
        self.assertEqual(st['gated'][0]['reason'], gate['gate']['reason'])
        for who in ({'session': 's2'}, {}, {'session': ' '}):  # outra sessão, nenhuma, ou o header vazio
            _, other = self.call('status', **who)
            self.assertNotIn('gate_id', other['gated'][0], who)
            self.assertEqual(other['gated'][0]['reason'], gate['gate']['reason'])
        _, by_arg = self.call('status', {'caller': 's'})  # sem header, vale o argumento
        self.assertEqual(by_arg['gated'][0]['gate_id'], gate['gate']['id'])
        err, _ = self.call('dispatch', {'task': 'g', 'role': 'executor', 'approved': gate['gate']['id'],
                                        'caller': 'x'}, session='s2')
        self.assertTrue(err)  # e o id que outra sessão não vê não a deixa aprovar

    def test_queued_answers_with_the_real_state_when_the_request_is_no_longer_in_the_queue(self):  # P3 O-A10
        rid = self.queue.enqueue(task='a', role='consultant')
        self.queue.mark_running(rid)  # o ciclo o abriu entre o enqueue e a resposta
        out = self.service.queued(rid)
        self.assertEqual((out['request'], out['state'], out['position']), (rid, 'running', None))


if __name__ == '__main__':
    unittest.main()
