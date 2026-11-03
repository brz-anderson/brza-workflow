"""A ponta a ponta do orquestrador: o servidor de verdade numa thread, com o CLI do client, o Ollama, o `claude` e as
contas trocados por dublês, e o `measurements.jsonl` com as linhas `route` na ordem.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import os
import unittest
from pathlib import Path

from mcp_support import ServerCase

from brza_mcp import cli  # noqa: E402
from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402
from brza_mcp import resolver as res  # noqa: E402
from test_mcp_cycle import FakeCli  # noqa: E402


class NoAccounts:
    def usage(self, account, runtime):
        return None


class EndToEndTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.ws = self.tmp / 'workspace'
        self.ws.mkdir()
        os.environ['BRZA_WORKSPACE'] = str(self.ws)
        self.cli = FakeCli()
        self.ollama_calls = []

        def ollama(url, body, timeout):
            self.ollama_calls.append(body['state'])
            return {'answers': {'layer': {'choice': 'alta', 'confidence': 0.9}}}

        def claude(argv, input, env, timeout):  # noqa: A002
            raise AssertionError('o claude real não pode rodar')
        self.queue = q.Queue(':memory:')
        self.addCleanup(self.queue.close)
        limits = lim.Limits({'orchestrator.max_workers': 1}, machine=lambda: {'free_mem_gb': 8.0, 'swap_used_gb': 0.1,
                                                                              'cpu_percent': 20})
        resolver = res.Resolver(config=lambda: {'resolver.tev1_min_confidence': 0.5}, ollama=ollama, claude=claude)
        self.service, self.scheduler = cli.build(self.registry, queue=self.queue, accounts=NoAccounts(),
                                                 run=self.cli, detect=lambda: 'studio', interval=3600,
                                                 limits=limits, resolver=resolver)

    def call(self, name, arguments, session='sess-1'):
        status, _, msg = self.rpc('tools/call', {'name': name, 'arguments': arguments},
                                  headers={'X-Brza-Session': session, 'X-Brza-Client': 'studio'})
        self.assertEqual(status, 200)
        text = msg['result']['content'][0]['text']
        return json.loads(text)

    def rows(self):
        path = Path(self.ws) / '.measurements' / 'measurements.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()]

    def seq(self, request=None):
        return [r['decision'] for r in self.rows() if request is None or r['request'] == request]

    def test_two_consultants_an_executor_gate_and_an_unknown_role(self):
        # o primeiro consultor entra, e o ciclo o abre pelo CLI dublê
        first = self.call('dispatch', {'task': 'ler o plano', 'role': 'consultant'})
        self.assertEqual((first['state'], first['held_by']), ('queued', None))
        self.assertEqual(self.scheduler.tick(), [first['request']])
        self.assertEqual(self.cli.verbs(), ['run-create', 'task-create', 'worker-start'])
        # o segundo é segurado pelo teto do workspace
        second = self.call('dispatch', {'task': 'ler o resto', 'role': 'consultant'})
        self.assertEqual((second['held_by'], second['position']), ('workspace', 1))
        self.assertEqual(self.scheduler.tick(), [])
        # o fim do primeiro chega pelo task-list dublê, o teto se libera e o segundo sai
        self.cli.task_status = 'completed'
        self.assertEqual(self.scheduler.tick(), [second['request']])
        self.assertEqual(self.cli.verbs()[3:], ['task-list', 'task-list', 'run-create', 'task-create', 'worker-start'])
        self.assertEqual(self.scheduler.tick(), [])  # o task-list dublê já diz completed: o segundo fecha
        self.assertEqual(self.queue.get(second['request'])['state'], 'done')
        # o executor devolve o gate, e o approved o admite
        gate = self.call('dispatch', {'task': 'implementar', 'role': 'executor'})['gate']
        approved = self.call('dispatch', {'task': 'implementar', 'role': 'executor', 'approved': gate['id']})
        self.assertEqual(approved['state'], 'queued')
        self.assertIsNone(approved['held_by'])
        # o papel desconhecido passa pelo desempate do resolver dublê (e pede o gate)
        unknown = self.call('dispatch', {'task': 'desenhar o contrato', 'role': 'pesquisador'})
        self.assertIn('gate', unknown)
        self.assertEqual(self.ollama_calls, ['desenhar o contrato'])

        rows = self.rows()
        self.assertTrue(all(r['event'] == 'route' for r in rows))
        self.assertEqual(self.seq(first['request']), ['policy', 'queued', 'admitted', 'done'])
        self.assertEqual(self.seq(second['request']), ['policy', 'queued', 'held', 'admitted', 'done'])
        self.assertEqual([r['decision'] for r in rows],
                         ['policy', 'queued', 'admitted',                      # primeiro
                          'policy', 'queued', 'held',                         # segundo, segurado
                          'done', 'admitted', 'done',                         # fim do primeiro, segundo sai e fecha
                          'policy', 'gated', 'approved', 'queued', 'admitted',  # executor
                          'resolved', 'policy', 'gated'])                     # papel desconhecido
        held = [r for r in rows if r['decision'] == 'held'][0]
        self.assertEqual((held['held_by'], held['request']), ('workspace', second['request']))
        self.assertEqual(held['measures']['workspace'], {'running': 1, 'max_workers': 1})
        resolved = [r for r in rows if r['decision'] == 'resolved'][0]
        self.assertEqual(resolved['resolver']['path'], 'tev1')
        self.assertEqual(resolved['caller'], 'sess-1')
        stamps = [r['timestamp'] for r in rows]
        self.assertEqual(stamps, sorted(stamps))


if __name__ == '__main__':
    unittest.main()
