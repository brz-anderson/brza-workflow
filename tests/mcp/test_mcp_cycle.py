"""Testes do ciclo do worker (D6, M13, M14, H4): o CLI do cliente trocado, o env do H4 e o fim que libera o teto.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import unittest
from unittest.mock import patch

from mcp_support import paths  # noqa: F401
from tools_support import ToolsCase

from brza_mcp import cli, cycle  # noqa: E402
from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402
from brza_mcp.registry import ToolRegistry  # noqa: E402


class FakeCli:
    """O `orchestration` do cliente: grava cada chamada (argv, env) e responde com o recibo `{ok, result}`."""

    def __init__(self):
        self.calls = []
        self.task_status = 'dispatched'
        self.fail = {}

    def __call__(self, argv, env, timeout):
        self.calls.append((argv, env))
        verb = argv[2]
        if verb in self.fail:
            return 1, json.dumps({'ok': False, 'error': {'code': self.fail[verb]}}), ''
        result = {'run-create': {'runId': 'run_1'}, 'task-create': {'taskId': 'task_1'},
                  'worker-start': {'dispatch': {'dispatchId': 'disp_1'}},
                  'task-list': {'tasks': [{'id': 'task_1', 'status': self.task_status}]}}[verb]
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
        self.assertEqual(argv[0], 'orca')
        self.assertTrue(env['ORCA_USER_DATA_PATH'].endswith('Application Support/orca'))

    def test_without_the_header_the_client_comes_from_the_live_process(self):
        queue, scheduler, cli_ = make(detected='orca')
        add(queue)
        scheduler.tick()
        self.assertEqual(cli_.calls[0][0][0], 'orca')

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

    def test_a_poll_error_keeps_the_worker_running(self):
        queue, scheduler, cli_ = make()
        rid = add(queue, client='studio')
        scheduler.tick()
        cli_.fail['task-list'] = 'offline'
        scheduler.tick()
        self.assertEqual(queue.get(rid)['state'], 'running')


class ThreadTest(unittest.TestCase):
    def test_the_thread_opens_on_kick_and_stops_clean(self):
        queue, scheduler, cli_ = make()
        scheduler.interval = 60
        rid = add(queue, client='studio')
        scheduler.start()
        scheduler.kick()
        for _ in range(100):
            if queue.get(rid)['state'] == 'running':
                break
            scheduler.stop_event.wait(0.05)
        scheduler.stop()
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertFalse(scheduler.thread.is_alive())


class ClientHeaderTest(ToolsCase):
    def test_the_header_is_stored_in_the_request_and_dispatch_kicks_the_scheduler(self):
        kicks = []
        self.service.on_queued = lambda: kicks.append(1)
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client='orca')
        self.assertEqual(self.queue.get(out['request'])['client'], 'orca')
        self.assertEqual(kicks, [1])
        _, out = self.call('dispatch', {'task': 'ler', 'role': 'consultant'}, session='s1', client='vscode')
        self.assertIsNone(self.queue.get(out['request'])['client'])  # valor fora de studio e orca é ignorado


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
