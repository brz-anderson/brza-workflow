"""Testes da telemetria `route` (D5, PLN-003/M16): uma linha por decisão no `.measurements/measurements.jsonl` do
workspace, sem nunca derrubar o pedido.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from mcp_support import HomeCase, paths  # noqa: F401
from tools_support import ToolsCase

from brza_mcp import cycle, telemetry  # noqa: E402
from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402
from brza_mcp import resolver as res  # noqa: E402
from brza_mcp import tools  # noqa: E402
from brza_mcp.registry import ToolRegistry  # noqa: E402
from test_mcp_cycle import FakeCli  # noqa: E402


def read_lines(workspace):
    path = Path(workspace) / '.measurements' / 'measurements.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class WorkspaceCase(ToolsCase):
    """O `ToolsCase` com o workspace temporário e a telemetria ligada nele."""

    def setUp(self):
        super().setUp()
        self.errors = []
        self.ws = self.tmp / 'ws'
        self.ws.mkdir()
        self.telemetry = telemetry.Telemetry(str(self.ws), workstream='tools-kit', log=self.errors.append)
        self.service.telemetry = self.telemetry
        self.queue.on_decision = self.telemetry.decided

    def scheduler(self, detected='studio'):
        """O ciclo com o CLI dublê e a telemetria deste caso: o `admitted` só sai quando ele abre o worker."""
        self.cli = FakeCli()
        return cycle.Scheduler(self.queue, self.limits, run=self.cli, detect=lambda: detected, telemetry=self.telemetry,
                               prepare=self.service.prepare)

    def lines(self, decision=None):
        rows = read_lines(self.ws)
        return [r for r in rows if decision is None or r['decision'] == decision]


class LineTest(WorkspaceCase):
    machine = staticmethod(lambda: {'free_mem_gb': 9.1, 'swap_gb': 0.4, 'cpu_percent': 31})

    def test_a_queued_request_writes_the_d5_line(self):
        _, out = self.call('dispatch', {'task': 'ler o plano', 'role': 'consultant'}, session='sess-1')
        rows = self.lines()
        self.assertEqual([r['decision'] for r in rows], ['policy', 'queued'])  # o `admitted` é do ciclo, e ele não rodou
        queued = rows[1]
        self.assertEqual(queued['event'], 'route')
        self.assertEqual(queued['workstream'], 'tools-kit')
        self.assertEqual((queued['caller'], queued['request']), ('sess-1', out['request']))
        self.assertEqual((queued['model'], queued['effort']), ('sonnet', 'low'))
        self.assertRegex(queued['timestamp'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        self.assertNotIn('ts', queued)
        for key in ('account', 'reason', 'held_by', 'machine', 'resolver'):
            self.assertIn(key, queued)

    def test_the_policy_line_carries_model_effort_reason_and_passthrough(self):
        self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s')
        policy = self.lines('policy')[0]
        self.assertEqual((policy['model'], policy['effort'], policy['reason']), ('sonnet', 'low', 'policy'))
        self.assertIs(policy['passthrough'], False)
        self.call('config', {'key': 'policy.enforce', 'value': False})
        self.call('dispatch', {'task': 'ler', 'role': 'consultant', 'model': 'haiku'}, session='s')
        policy = self.lines('policy')[1]
        self.assertEqual((policy['model'], policy['reason'], policy['passthrough']), ('haiku', 'passthrough', True))

    def test_admission_is_written_when_the_cycle_opens_the_worker_with_the_measures_and_only_once(self):
        scheduler = self.scheduler()
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s')
        self.call('status')
        self.call('status')
        self.assertEqual(self.lines('admitted'), [])  # a prévia do `status` e do `queued()` não admite nada
        self.assertEqual(scheduler.tick(), [out['request']])
        scheduler.tick()
        scheduler.tick()
        admitted = self.lines('admitted')
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0]['request'], out['request'])
        self.assertEqual(admitted[0]['machine'], {'free_mem_gb': 9.1, 'swap_gb': 0.4, 'cpu_percent': 31,
                                                  'enforced': False})
        self.assertIsNone(admitted[0]['held_by'])
        self.assertEqual(admitted[0]['measures']['workspace']['running'], 0)

    def test_a_request_without_a_client_writes_no_admitted_or_held_line_on_every_round(self):  # P6 final A1
        scheduler = self.scheduler(detected=None)  # nenhum cliente de pé: o ciclo o libera e põe no `skipped`
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s')
        before = len(self.lines())
        for _ in range(100):
            scheduler.tick()
        self.assertEqual(len(self.lines()), before)
        self.assertEqual((self.lines('admitted'), self.queue.get(out['request'])['state']), ([], 'queued'))
        self.assertEqual(self.queue.running_count(), 0)

    def test_the_held_and_admitted_do_not_alternate_when_the_first_cannot_open(self):  # P6 final A1
        self.queue.mark_running(self.queue.enqueue(task='ocupa', role='consultant', caller='s'))
        self.limits.config = {'orchestrator.max_workers': 2}
        scheduler = self.scheduler(detected=None)
        self.queue.enqueue(task='a', role='consultant', caller='s')
        self.queue.enqueue(task='b', role='consultant', caller='s')
        for _ in range(30):
            scheduler.tick()
        decisions = [(r['request'], r['decision']) for r in self.lines()]
        self.assertLessEqual(len(decisions), 2, decisions)
        self.assertNotIn('admitted', [d for _, d in decisions])

    def test_the_gate_and_its_approval_are_lines(self):
        _, gate = self.call('dispatch', {'task': 'x', 'role': 'executor'}, session='s')
        self.assertEqual([r['decision'] for r in self.lines()], ['policy', 'gated'])
        gated = self.lines('gated')[0]
        self.assertEqual(gated['gate'], gate['gate']['id'])
        self.assertIn('executor', gated['reason'])
        self.call('dispatch', {'task': 'x', 'role': 'executor', 'approved': gate['gate']['id']}, session='s')
        self.assertEqual([r['decision'] for r in self.lines()], ['policy', 'gated', 'approved', 'queued'])
        self.scheduler().tick()
        self.assertEqual([r['decision'] for r in self.lines()], ['policy', 'gated', 'approved', 'queued', 'admitted'])
        self.assertEqual(self.lines('approved')[0]['request'], gated['request'])


class HeldTest(WorkspaceCase):
    config = {'orchestrator.max_workers': 1}

    def test_a_held_request_writes_the_ceiling_the_reason_and_the_measures_once(self):
        first = self.queue.enqueue(task='a', role='consultant', caller='s')
        self.queue.mark_running(first)
        _, out = self.call('dispatch', {'task': 'b', 'role': 'consultant'}, session='s')
        self.assertEqual(out['held_by'], 'workspace')
        self.call('status')
        held = self.lines('held')
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]['held_by'], 'workspace')
        self.assertIn('max_workers', held[0]['reason'])
        self.assertEqual(held[0]['measures']['workspace'], {'running': 1, 'max_workers': 1})
        self.assertEqual(held[0]['request'], out['request'])

    def test_a_request_that_is_admissible_but_not_the_first_has_no_admitted_line(self):
        self.queue.enqueue(task='a', role='consultant', caller='s', priority='high')
        second = self.queue.enqueue(task='b', role='consultant', caller='s')
        self.queue.next_admissible(self.limits)
        self.assertEqual(self.lines('admitted'), [])  # a prévia não admite
        self.scheduler().tick()  # com o teto de 1 worker, só o primeiro abre
        self.assertEqual([r['request'] for r in self.lines('admitted')], [1])
        self.assertNotIn(second, [r['request'] for r in self.lines('admitted')])
        self.assertEqual([r['held_by'] for r in self.lines('held')], ['workspace'])


class ResolverLineTest(WorkspaceCase):
    def test_the_tiebreak_line_has_path_confidence_and_time(self):
        fake = {'answers': {'layer': {'choice': 'alta', 'confidence': 0.9}}}
        self.service.resolver = res.Resolver(config=lambda: {'resolver.tev1_min_confidence': 0.5},
                                             ollama=lambda url, body, timeout: fake)
        self.call('dispatch', {'task': 'desenhar a fila', 'role': 'pesquisador'}, session='s')
        resolved = self.lines('resolved')[0]
        self.assertEqual(resolved['resolver']['path'], 'tev1')
        self.assertEqual(resolved['resolver']['confidence'], 0.9)
        self.assertEqual(resolved['resolver']['layer'], 'alta')
        self.assertIn('ms', resolved['resolver'])
        self.assertEqual(self.lines('policy')[0]['model'], 'opus')
        self.assertEqual([r['decision'] for r in self.lines()][:3], ['resolved', 'policy', 'gated'])

    def test_the_network_call_does_not_hold_the_queue_lock(self):
        outcome = []

        def probe(url, body, timeout):  # outra thread: o RLock é reentrante, e só ela mostra se alguém o segura
            def grab():
                got = self.queue.lock.acquire(timeout=1)
                outcome.append(got)
                if got:
                    self.queue.lock.release()
            t = threading.Thread(target=grab)
            t.start()
            t.join()
            raise OSError('sem Ollama')
        self.service.resolver = res.Resolver(ollama=probe, claude=lambda *a: (1, ''))
        self.call('dispatch', {'task': 'x', 'role': 'pesquisador'}, session='s')
        self.assertEqual(outcome, [True])


class CycleLineTest(WorkspaceCase):
    def test_the_end_of_the_worker_is_done_or_failed(self):
        cli_ = FakeCli()
        scheduler = cycle.Scheduler(self.queue, self.limits, run=cli_, detect=lambda: 'studio',
                                    telemetry=self.telemetry)
        a = self.queue.enqueue(task='a', role='consultant', caller='s', client='studio')
        scheduler.tick()
        cli_.task_status = 'completed'
        scheduler.tick()
        done = self.lines('done')
        self.assertEqual([(r['request'], r['caller']) for r in done], [(a, 's')])
        b = self.queue.enqueue(task='b', role='consultant', caller='s', client='studio')
        cli_.task_status = 'dispatched'
        scheduler.tick()
        cli_.task_status = 'failed'
        scheduler.tick()
        self.assertEqual([r['request'] for r in self.lines('failed')], [b])

    def test_a_cli_failure_on_open_is_a_failed_line_with_the_reason(self):
        cli_ = FakeCli()
        cli_.fail['run-create'] = 'boom'
        scheduler = cycle.Scheduler(self.queue, self.limits, run=cli_, detect=lambda: 'studio',
                                    telemetry=self.telemetry)
        rid = self.queue.enqueue(task='a', role='consultant', caller='s', client='studio')
        scheduler.tick()
        failed = self.lines('failed')[0]
        self.assertEqual(failed['request'], rid)
        self.assertIn('run-create', failed['reason'])


class SafetyTest(HomeCase):
    def test_without_a_workspace_nothing_is_written_and_nothing_raises(self):
        log = []
        t = telemetry.Telemetry(None, log=log.append)
        self.assertFalse(t.emit('queued', {'id': 1}))
        self.assertFalse(t.emit('queued', {'id': 2}))
        self.assertEqual(list(self.tmp.rglob('measurements.jsonl')), [])
        self.assertEqual(len(log), 1)  # P6 final A2: o motivo vai uma vez ao log
        self.assertIn('BRZA_WORKSPACE', log[0])

    def test_dispatch_answers_without_BRZA_WORKSPACE(self):
        class Ctx:
            session, headers = 's', {}
        queue = q.Queue(':memory:')
        self.addCleanup(queue.close)
        service = tools.install(ToolRegistry(), queue=queue, limits=lim.Limits({}, machine=lambda: {}))
        self.assertIsNone(service.telemetry.path)
        out = service.dispatch({'task': 'ler', 'role': 'consultant'}, Ctx())
        self.assertEqual(out['state'], 'queued')

    def test_a_workspace_that_cannot_be_written_logs_once_and_does_not_raise(self):
        blocked = self.tmp / 'ro'
        blocked.mkdir()
        (blocked / '.measurements').write_text('é um arquivo, não um diretório')
        errors = []
        t = telemetry.Telemetry(str(blocked), log=errors.append)
        self.assertFalse(t.emit('queued', {'id': 1}))
        self.assertFalse(t.emit('queued', {'id': 2}))
        self.assertEqual(len(errors), 1)
        self.assertIn('measurements', errors[0])

    def test_a_failing_hook_never_reaches_the_queue(self):
        t = telemetry.Telemetry(str(self.tmp), log=lambda m: None)
        t.emit = lambda *a, **k: 1 / 0
        queue = q.Queue(':memory:')
        self.addCleanup(queue.close)
        queue.on_decision = t.decided
        queue.enqueue(task='a', role='consultant')
        self.assertIsNotNone(queue.next_admissible(lim.Limits({}, machine=lambda: {})))


class ConcurrencyTest(HomeCase):
    def test_two_threads_writing_do_not_interleave_lines(self):
        t = telemetry.Telemetry(str(self.tmp), workstream='x')
        big = 'é' * 3000

        def worker(tag):
            for i in range(60):
                t.emit('held', {'id': i, 'caller': tag}, reason=big, measures={'tag': tag})
        threads = [threading.Thread(target=worker, args=(tag,)) for tag in ('a', 'b')]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        rows = read_lines(self.tmp)
        self.assertEqual(len(rows), 120)
        self.assertEqual({r['caller'] for r in rows}, {'a', 'b'})
        self.assertTrue(all(r['reason'] == big for r in rows))

    def test_the_file_is_opened_in_append_mode_with_one_write_per_line(self):
        t = telemetry.Telemetry(str(self.tmp), workstream='x')
        t.emit('queued', {'id': 1})
        t.emit('queued', {'id': 2})
        text = (self.tmp / '.measurements' / 'measurements.jsonl').read_text()
        self.assertEqual(text.count('\n'), 2)
        self.assertTrue(text.endswith('\n'))


class WorkstreamTest(HomeCase):
    def git(self, *args):
        subprocess.run(['git', '-C', str(self.tmp / 'repo'), *args], check=True, capture_output=True)

    def test_the_workstream_comes_from_the_branch_of_the_workspace(self):
        (self.tmp / 'repo').mkdir()
        self.git('init', '-q', '-b', 'workstream/tools-kit')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x')
        t = telemetry.Telemetry(str(self.tmp / 'repo'))
        t.emit('queued', {'id': 1})
        self.assertEqual(read_lines(self.tmp / 'repo')[0]['workstream'], 'tools-kit')

    def test_without_a_workspace_the_workstream_is_null_and_git_is_not_called(self):  # P6 F-A1
        with patch('brza_mcp.telemetry.subprocess.run', side_effect=AssertionError('sem workspace não há git')):
            self.assertIsNone(telemetry.Telemetry(None).workstream())
            self.assertIsNone(telemetry.Telemetry('').workstream())

    def test_without_git_the_workstream_is_null(self):
        t = telemetry.Telemetry(str(self.tmp))
        t.emit('queued', {'id': 1})
        self.assertIsNone(read_lines(self.tmp)[0]['workstream'])


if __name__ == '__main__':
    unittest.main()
