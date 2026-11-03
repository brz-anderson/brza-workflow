"""Testes do ciclo do worker (D6, M13, M14, H4): o CLI do cliente trocado, o env do H4 e o fim que libera o teto.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_support import paths  # noqa: F401
from tools_support import ToolsCase

from brza_mcp import cli, clients, cycle  # noqa: E402
from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402
from brza_mcp.registry import ToolRegistry  # noqa: E402


class FakeCli:
    """O `orchestration` do cliente: grava cada chamada (argv, env) e responde com o recibo `{ok, result}`."""

    def __init__(self):
        self.calls = []
        self.task_status = 'dispatched'
        self.fail = {}
        self.fail_body = {}  # o corpo extra do erro de um verbo, como o `dispatchId` do worker-start que falhou

    def __call__(self, argv, env, timeout):
        self.calls.append((argv, env))
        verb = argv[2]
        if verb in self.fail:
            return 1, json.dumps({'ok': False, 'error': {'code': self.fail[verb]}, **self.fail_body.get(verb, {})}), ''
        result = {'run-create': {'runId': 'run_1'}, 'task-create': {'taskId': 'task_1'},
                  'worker-start': {'dispatch': {'dispatchId': 'disp_1'}},
                  'task-list': {'tasks': [{'id': 'task_1', 'status': self.task_status}]},
                  'worker-release': {'state': 'released'}, 'task-update': {'ok': True},
                  'worker-show': {'dispatch': {'dispatchId': 'disp_1'}}}[verb]
        return 0, json.dumps({'ok': True, 'result': result}), ''

    def verbs(self):
        return [argv[2] for argv, _ in self.calls]


def make(config=None, **kw):
    queue = q.Queue(':memory:')
    limits = lim.Limits(config or {}, machine=lambda: {})
    cli_ = FakeCli()
    scheduler = cycle.Scheduler(queue, limits, run=cli_, detect=lambda: kw.get('detected'))
    return queue, scheduler, cli_


def add(queue, **kw):
    kw.setdefault('task', 'Ler o plano\nmais linhas')
    kw.setdefault('role', 'consultant')
    kw.setdefault('caller', 'sess-1')
    return queue.enqueue(**kw)


class OpenTest(unittest.TestCase):
    def test_the_admitted_request_opens_through_the_cli_with_the_h4_env_and_becomes_running(self):
        queue, scheduler, cli_ = make()
        rid = add(queue, model='sonnet', effort='low', client='studio')
        self.assertEqual(scheduler.tick(), [rid])
        self.assertEqual(cli_.verbs(), ['run-create', 'task-create', 'worker-start'])
        argv, env = cli_.calls[0]
        self.assertEqual(argv[0], '/Applications/BrzA Studio.app/Contents/Resources/bin/brza')
        self.assertEqual(argv[1:], ['orchestration', 'run-create', '--objective', 'Ler o plano\nmais linhas', '--json'])
        for _, env in cli_.calls:
            self.assertEqual(env['ORCA_AGENT_SESSION_ID'], 'sess-1')
            self.assertTrue(env['ORCA_USER_DATA_PATH'].endswith('Application Support/brza-studio'))
        self.assertEqual(cli_.calls[1][0][3:], ['--run', 'run_1', '--task-title', 'Ler o plano',
                                                '--spec', 'Ler o plano\nmais linhas', '--json'])
        self.assertEqual(cli_.calls[2][0][3:], ['--task', 'task_1', '--run', 'run_1', '--agent', 'claude',
                                                '--model', 'sonnet', '--effort', 'low', '--json'])
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertEqual(queue.running_count(), 1)
        state = json.loads(queue._all('SELECT session FROM workers')[0]['session'])
        self.assertEqual(state, {'client': 'studio', 'run': 'run_1', 'task': 'task_1', 'dispatch': 'disp_1'})

    def test_orca_header_picks_the_orca_cli_and_data_dir(self):
        queue, scheduler, cli_ = make()
        add(queue, client='orca')
        scheduler.tick()
        argv, env = cli_.calls[0]
        self.assertTrue(argv[0].endswith('/orca'))
        self.assertTrue(env['ORCA_USER_DATA_PATH'].endswith('Application Support/orca'))

    def test_without_the_header_the_client_comes_from_the_live_process(self):
        queue, scheduler, cli_ = make(detected='orca')
        add(queue)
        scheduler.tick()
        self.assertTrue(cli_.calls[0][0][0].endswith('/orca'))

    def test_without_header_and_without_a_live_client_the_request_stays_queued(self):
        queue, scheduler, cli_ = make()
        rid = add(queue)
        self.assertEqual(scheduler.tick(), [])
        self.assertEqual((cli_.calls, queue.get(rid)['state']), ([], 'queued'))
        self.assertIn('cliente', scheduler.errors[rid])

    def test_inherited_orca_env_does_not_leak_into_the_subprocess(self):
        queue, scheduler, cli_ = make()
        add(queue, client='studio')
        with patch.dict('os.environ', {'ORCA_TERMINAL_HANDLE': 'h', 'ORCA_AGENT_SESSION_ID': 'outra'}):
            scheduler.tick()
        env = cli_.calls[0][1]
        self.assertNotIn('ORCA_TERMINAL_HANDLE', env)
        self.assertEqual(env['ORCA_AGENT_SESSION_ID'], 'sess-1')

    def test_the_cli_failure_fails_the_request_without_holding_the_ceiling(self):
        queue, scheduler, cli_ = make({'orchestrator.max_workers': 1})
        first, second = add(queue, client='studio'), add(queue, client='studio')
        cli_.fail['task-create'] = 'boom'
        scheduler.tick()
        self.assertEqual(queue.get(first)['state'], 'failed')
        self.assertEqual(queue.get(second)['state'], 'failed')  # o teto não ficou ocupado: o seguinte também tentou
        self.assertIn('boom', scheduler.errors[first])
        self.assertEqual(queue.running_count(), 0)


    def test_no_client_for_the_first_request_does_not_hold_the_ones_behind_it(self):  # P5 O-A8 / F-A5
        logs = []
        queue, scheduler, cli_ = make()
        scheduler.log = logs.append
        first, second = add(queue), add(queue, client='studio')
        self.assertEqual(scheduler.tick(), [second])
        self.assertEqual((queue.get(first)['state'], queue.get(second)['state']), ('queued', 'running'))
        self.assertTrue(any(f'pedido {first}' in m for m in logs))
        self.assertIn('cliente', queue.get(first)['error'])  # o `status` mostra o sinal
        scheduler.tick()
        self.assertEqual(sum(f'pedido {first}' in m for m in logs), 1)  # o mesmo erro não se repete a cada volta

    def test_a_request_without_a_client_that_opens_later_loses_the_error(self):
        queue, scheduler, _ = make()
        rid = add(queue)
        scheduler.tick()
        self.assertIsNotNone(queue.get(rid)['error'])
        scheduler.detect = lambda: 'studio'
        scheduler.tick()
        self.assertEqual((queue.get(rid)['state'], queue.get(rid)['error']), ('running', None))

    def test_the_run_and_the_task_are_saved_in_the_worker_before_the_worker_start(self):  # P5 O-A5
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        seen = {}
        real = cli_.__call__

        def spy(argv, env, timeout):
            if argv[2] == 'worker-start':
                seen['session'] = json.loads(queue._all('SELECT session FROM workers')[0]['session'])
            return real(argv, env, timeout)
        scheduler.run = spy
        scheduler.tick()
        self.assertEqual(seen['session'], {'client': 'studio', 'run': 'run_1', 'task': 'task_1', 'dispatch': None})
        self.assertEqual(queue.get(rid)['state'], 'running')

    def test_a_start_failed_releases_the_worker_and_fails_the_task(self):  # P5 O-A5
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        cli_.fail['worker-start'] = 'start_failed'
        cli_.fail_body['worker-start'] = {'dispatchId': 'disp_9'}
        scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'failed')
        self.assertIn('start_failed', queue.get(rid)['error'])
        verbs = cli_.verbs()
        self.assertEqual(verbs[-2:], ['worker-release', 'task-update'])
        release = next(a for a, _ in cli_.calls if a[2] == 'worker-release')
        self.assertEqual(release[3:5], ['--dispatch', 'disp_9'])
        update = next(a for a, _ in cli_.calls if a[2] == 'task-update')
        self.assertEqual(update[3:7], ['--id', 'task_1', '--status', 'failed'])
        self.assertEqual(queue.running_count(), 0)

    def test_a_start_outcome_unknown_keeps_the_request_running_for_the_poll_to_decide(self):  # P5 O-A5
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        cli_.fail['worker-start'] = 'start_outcome_unknown'
        cli_.fail_body['worker-start'] = {'dispatchId': 'disp_7'}
        self.assertEqual(scheduler.tick(), [rid])
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertNotIn('worker-release', cli_.verbs())
        state = json.loads(queue._all('SELECT session FROM workers')[0]['session'])
        self.assertEqual((state['task'], state['dispatch']), ('task_1', 'disp_7'))
        cli_.fail.clear()
        cli_.task_status = 'completed'
        scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'done')

    def test_a_worker_left_without_a_session_by_a_crash_is_released_at_the_start(self):
        queue, scheduler, _ = make()
        rid = add(queue)  # sem cliente: a primeira volta da thread não o reabre
        queue.admit_next(scheduler.limits)  # a queda veio logo depois da reserva
        self.assertEqual(queue.running_count(), 1)
        scheduler.interval = 60
        scheduler.start()
        scheduler.stop()
        self.assertEqual((queue.running_count(), queue.get(rid)['state']), (0, 'queued'))


class EndTest(unittest.TestCase):
    def test_the_finished_task_marks_done_releases_the_ceiling_and_the_next_request_goes_out(self):
        queue, scheduler, cli_ = make({'orchestrator.max_workers': 1})
        first, second = add(queue, client='studio'), add(queue, client='studio')
        self.assertEqual(scheduler.tick(), [first])
        self.assertEqual(queue.get(second)['state'], 'queued')
        self.assertEqual(queue.get(second)['held_by'], 'workspace')
        self.assertEqual(scheduler.tick(), [])  # a task segue `dispatched`
        cli_.task_status = 'completed'
        self.assertEqual(scheduler.tick(), [second])
        self.assertEqual(queue.get(first)['state'], 'done')
        self.assertIsNotNone(queue.get(first)['finished_at'])
        self.assertEqual(queue.get(second)['state'], 'running')
        self.assertEqual(cli_.calls[-4][0][2], 'task-list')

    def test_the_failed_task_marks_the_request_failed(self):
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.task_status = 'failed'
        scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'failed')
        self.assertEqual(queue.running_count(), 0)

    def test_the_poll_uses_the_caller_env(self):
        queue, scheduler, cli_ = make()
        add(queue, client='studio')
        scheduler.tick()
        scheduler.tick()
        argv, env = cli_.calls[-1]
        self.assertEqual(argv[2:5], ['task-list', '--run', 'run_1'])
        self.assertEqual(env['ORCA_AGENT_SESSION_ID'], 'sess-1')

    def test_a_poll_error_keeps_the_worker_running_for_a_few_rounds(self):
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.fail['task-list'] = 'offline'
        for _ in range(cycle.POLL_FAILURES - 1):
            scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'running')
        cli_.task_status = 'completed'
        cli_.fail.clear()
        scheduler.tick()  # a consulta voltou: a contagem zera
        self.assertEqual(queue.get(rid)['state'], 'done')

    def test_a_worker_the_client_no_longer_knows_is_failed_after_the_poll_keeps_failing(self):  # P5 O-A12
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.fail['task-list'] = 'offline'
        cli_.fail['worker-show'] = 'dispatch_not_found'
        for _ in range(cycle.POLL_FAILURES):
            scheduler.tick()
        row = queue.get(rid)
        self.assertEqual(row['state'], 'failed')
        self.assertIn('sumiu', row['error'])
        self.assertEqual(queue.running_count(), 0)

    def test_a_worker_the_client_still_knows_stays_running(self):  # P5 O-A12
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.fail['task-list'] = 'offline'
        for _ in range(cycle.POLL_FAILURES + 2):
            scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertIn('worker-show', cli_.verbs())

    def test_the_failed_task_keeps_a_reason_for_the_caller(self):  # P5 O-A3
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.task_status = 'failed'
        scheduler.tick()
        self.assertIn('failed', queue.get(rid)['error'])


class ThreadTest(unittest.TestCase):
    def test_the_thread_opens_on_kick_and_stops_clean(self):
        queue, scheduler, cli_ = make()
        scheduler.interval = 60
        laps = []
        scheduler.prepare = lambda: laps.append(1)
        scheduler.start()
        for _ in range(200):  # a primeira volta passa e a thread dorme os 60 s: só o `kick` a acorda (P5 O-A6)
            if laps:
                break
            scheduler.stop_event.wait(0.01)
        scheduler.stop_event.wait(0.1)
        rid = add(queue, client='studio')
        scheduler.stop_event.wait(0.2)
        self.assertEqual(queue.get(rid)['state'], 'queued')
        scheduler.kick()
        for _ in range(100):
            if queue.get(rid)['state'] == 'running':
                break
            scheduler.stop_event.wait(0.05)
        scheduler.stop()
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertFalse(scheduler.thread.is_alive())


class MigrationTest(unittest.TestCase):
    OLD = """CREATE TABLE requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, role TEXT NOT NULL, target TEXT, runtime TEXT,
        model TEXT, effort TEXT, account TEXT, ref TEXT, deadline TEXT, blocks TEXT NOT NULL DEFAULT '[]',
        priority TEXT, caller TEXT, state TEXT NOT NULL DEFAULT 'queued', held_by TEXT, held_reason TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL);"""

    def test_a_database_of_the_p2_to_p4_gets_the_client_and_error_columns(self):  # P5 O-A6
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'o.db'
            conn = sqlite3.connect(db)
            conn.executescript(self.OLD)
            conn.execute("INSERT INTO requests (task, role, created_at, updated_at) VALUES ('velho', 'consultant', 1, 1)")
            conn.commit()
            conn.close()
            queue = q.Queue(db)
            rid = queue.enqueue(task='novo', role='consultant', client='studio')
            self.assertEqual(queue.get(rid)['client'], 'studio')
            self.assertIsNone(queue.get(1)['error'])
            queue.mark_done(rid, failed=True, error='boom')
            self.assertEqual(queue.get(rid)['error'], 'boom')
            queue.close()


class ClientHeaderTest(ToolsCase):
    def test_the_header_may_carry_the_user_data_path_of_the_client(self):  # P5 O-A7
        studio = clients.CLIENTS['studio']['user_data']
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client=studio)
        self.assertEqual(self.queue.get(out['request'])['client'], 'studio')
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1',
                           client='/Users/x/Library/Application Support/orca/')
        self.assertEqual(self.queue.get(out['request'])['client'], 'orca')
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client='/tmp/outro')
        self.assertIsNone(self.queue.get(out['request'])['client'])

    def test_the_header_is_stored_in_the_request_and_dispatch_kicks_the_scheduler(self):
        kicks = []
        self.service.on_queued = lambda: kicks.append(1)
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client='orca')
        self.assertEqual(self.queue.get(out['request'])['client'], 'orca')
        self.assertEqual(kicks, [1])
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client='vscode')
        self.assertIsNone(self.queue.get(out['request'])['client'])  # valor fora de studio e orca é ignorado


class ServeWiringTest(unittest.TestCase):
    def test_serve_starts_the_scheduler_before_it_serves(self):  # P5 O-A6
        from mcp_support import HomeCase
        started = {}

        class Fake(HomeCase):
            def runTest(self):
                pass
        case = Fake()
        case.setUp()
        try:
            queue = q.Queue(':memory:')
            service, scheduler = cli.build(ToolRegistry(), queue=queue, accounts=type('A', (), {
                'usage': lambda *a: None})(), run=FakeCli(), detect=lambda: None, interval=60)

            class FakeServer:
                server_address = ('127.0.0.1', 1)

                def serve_forever(self):
                    started['alive'] = scheduler.thread is not None and scheduler.thread.is_alive()

                def request_stop(self):
                    pass

                def server_close(self):
                    pass
            with patch.object(cli, 'build', return_value=(service, scheduler)), \
                    patch.object(cli.server, 'make_server', return_value=FakeServer()):
                cli.serve(0)
            self.assertTrue(started['alive'])
            self.assertFalse(scheduler.thread.is_alive())  # e o `finally` o parou
        finally:
            case.doCleanups()


class WiringTest(unittest.TestCase):
    def test_build_wires_the_account_provider_into_the_limits_and_the_kick_into_the_service(self):
        class Accounts:
            def usage(self, account, runtime):
                return None
        queue = q.Queue(':memory:')
        service, scheduler = cli.build(ToolRegistry(), queue=queue, accounts=Accounts(), run=FakeCli(),
                                       detect=lambda: None)
        self.assertEqual(service.limits.usage.__self__.__class__, Accounts)
        self.assertEqual(service.on_queued, scheduler.kick)
        self.assertEqual(scheduler.prepare, service.sync_limits)


if __name__ == '__main__':
    unittest.main()
