"""Testes do `dispatch`, do `status` e do schema das ferramentas, pelo servidor de verdade (D3, Pedido 2).

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import unittest

from tools_support import ToolsCase


class SchemaTest(ToolsCase):
    def test_the_three_tools_are_listed_with_the_d3_schema(self):
        _, _, msg = self.rpc('tools/list')
        tools = {t['name']: t['inputSchema'] for t in msg['result']['tools']}
        self.assertEqual(set(tools), {'dispatch', 'status', 'config'})
        self.assertEqual(tools['dispatch']['required'], ['task', 'role'])
        props = tools['dispatch']['properties']
        self.assertEqual(props['runtime'], {'enum': ['claude']})
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


class ValidationTest(ToolsCase):
    def refused(self, args, fragment):
        err, out = self.call('dispatch', args, session='s')
        self.assertTrue(err, out)
        self.assertIn(fragment, out)
        self.assertEqual(self.queue.pending(), [])

    def test_codex_is_refused(self):
        self.refused({'task': 't', 'role': 'consultant', 'runtime': 'codex'}, 'runtime')

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


if __name__ == '__main__':
    unittest.main()
