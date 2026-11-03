"""Testes dos gates do `dispatch` (R5.2, D3, Pedido 7) e da ferramenta `config`.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import unittest

from tools_support import ToolsCase


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

    def test_the_gate_survives_the_service(self):
        gate = self.gate({'task': 't', 'role': 'executor'})
        from brza_mcp import tools
        from brza_mcp.registry import ToolRegistry
        other = ToolRegistry()
        tools.install(other, queue=self.queue, limits=self.limits, workspace='/ws', index_reader=lambda w: '')
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
        for key in ('policy.roles.executor.model', 'mcp.port', 'foo', 'quota'):
            err, out = self.call('config', {'key': key, 'value': 1})
            self.assertTrue(err, key)
            self.assertIn(key, out)
        self.assertEqual(self.service.get_overrides(), {})

    def test_a_value_of_the_wrong_type_is_refused(self):
        self.assertTrue(self.call('config', {'key': 'policy.enforce', 'value': 'sim'})[0])
        self.assertTrue(self.call('config', {'key': 'orchestrator.max_workers', 'value': 'muitos'})[0])
        self.assertTrue(self.call('config', {'key': 'orchestrator.max_workers', 'value': True})[0])

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
