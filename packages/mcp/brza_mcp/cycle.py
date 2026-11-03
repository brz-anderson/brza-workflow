"""O ciclo do worker (D6): o `Scheduler` pede à fila o próximo pedido admissível e o abre pelo `orchestration` do cliente.

Abrir é, pelo `--help` do CLI do app: `run-create --objective`, `task-create --run --task-title --spec` e
`worker-start --task --agent claude --model --effort --run`, cada um com `--json`, e com `ORCA_AGENT_SESSION_ID` (o
`caller`, o `X-Brza-Session`) e `ORCA_USER_DATA_PATH` do cliente no env (H4). O CLI não tem flag de conta no
`worker-start`: a conta vale só para o `claude -p` (`accounts.claude_env`). O fim se sabe por consulta: a cada volta, o
`task-list --run` de cada worker aberto; a task `completed` fecha o pedido como `done` e a `failed` como `failed` (M14),
e o teto se libera no `mark_done`.

Quem roda o subprocesso é `run(argv, env, timeout) -> (código, stdout, stderr)`, injetável. O estado de cada worker
(`run`, `task`, `dispatch`) fica em JSON na coluna `session` da tabela `workers`, e sobrevive a um reinício.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

from . import clients as clients_mod

AGENT = 'claude'
DEFAULT_INTERVAL = 15.0
TASK_DONE = ('completed',)
TASK_FAILED = ('failed',)


class CycleError(RuntimeError):
    pass


class NoClient(CycleError):
    """Sem cliente para abrir o pedido: ele fica na fila até o Studio ou o Orca subir."""


def default_run(argv, env, timeout=120):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd='/tmp')
    except (OSError, subprocess.SubprocessError) as e:
        raise CycleError(f'{argv[0]}: {e}') from None
    return r.returncode, r.stdout, r.stderr


def find_key(obj, *keys):
    """O primeiro valor de alguma das `keys` no JSON, em qualquer profundidade (o CLI aninha o recibo)."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k) not in (None, ''):
                return obj[k]
        for v in obj.values():
            found = find_key(v, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, *keys)
            if found is not None:
                return found
    return None


def find_task(obj, task_id):
    if isinstance(obj, dict):
        if task_id in (obj.get('id'), obj.get('taskId')) and 'status' in obj:
            return obj
        for v in obj.values():
            found = find_task(v, task_id)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_task(v, task_id)
            if found:
                return found
    return None


class Scheduler:
    def __init__(self, queue, limits, run=default_run, detect=clients_mod.detect, prepare=None,
                 interval=DEFAULT_INTERVAL, log=None):
        self.queue, self.limits, self.run, self.detect = queue, limits, run, detect
        self.prepare = prepare  # o `sync_limits` do serviço: refaz a configuração antes de cada decisão
        self.interval = interval
        self.log = log or (lambda message: None)
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        self.tick_lock = threading.Lock()
        self.errors = {}

    # ---- o CLI ----

    def client_of(self, request):
        name = request.get('client') or self.detect()
        if name not in clients_mod.CLIENTS:
            raise NoClient('cliente desconhecido: sem X-Brza-Client no pedido e nenhum processo vivo do Studio ou Orca')
        return name

    def env_for(self, request, client):
        if not request.get('caller'):
            raise CycleError('o pedido não tem chamador: sem ORCA_AGENT_SESSION_ID o run-create cai noutro terminal (H4)')
        env = {k: v for k, v in os.environ.items() if not k.startswith('ORCA_')}
        env['ORCA_AGENT_SESSION_ID'] = request['caller']
        env['ORCA_USER_DATA_PATH'] = clients_mod.CLIENTS[client]['user_data']
        return env

    def cli(self, client, env, *args):
        argv = [clients_mod.CLIENTS[client]['cli'], 'orchestration', *args, '--json']
        code, out, err = self.run(argv, env, 120)
        try:
            body = json.loads(out)
        except ValueError:
            body = None
        if code != 0 or not isinstance(body, dict) or body.get('ok') is False:
            detail = json.dumps(body.get('error', body)) if isinstance(body, dict) else (err.strip() or out.strip())
            raise CycleError(f'orchestration {args[0]} falhou: {detail}')
        return body.get('result', body)

    # ---- abrir ----

    def open(self, request):
        """run-create, task-create e worker-start; devolve o estado do worker, que vai à coluna `session`."""
        client = self.client_of(request)
        env = self.env_for(request, client)
        run = find_key(self.cli(client, env, 'run-create', '--objective', request['task']), 'runId', 'run_id')
        if not run:
            raise CycleError('run-create não devolveu o runId')
        title = request['task'].strip().splitlines()[0][:80]
        task = find_key(self.cli(client, env, 'task-create', '--run', run, '--task-title', title,
                                 '--spec', request['task']), 'taskId', 'task_id')
        if not task:
            raise CycleError('task-create não devolveu o taskId')
        args = ['worker-start', '--task', task, '--run', run, '--agent', AGENT]
        if request.get('model'):
            args += ['--model', request['model']]
            if request.get('effort'):  # o CLI exige --model com --effort
                args += ['--effort', request['effort']]
        started = self.cli(client, env, *args)
        return {'client': client, 'run': run, 'task': task,
                'dispatch': find_key(started, 'dispatchId', 'dispatch_id')}

    def poll(self, request, state):
        """'done', 'failed' ou None (segue em curso), pela task do worker."""
        env = self.env_for(request, state['client'])
        listed = self.cli(state['client'], env, 'task-list', '--run', state['run'])
        task = find_task(listed, state['task'])
        status = (task or {}).get('status')
        if status in TASK_DONE:
            return 'done'
        if status in TASK_FAILED:
            return 'failed'
        return None

    # ---- a volta ----

    def tick(self):
        """Fecha os workers que terminaram e abre os pedidos que os tetos admitem. Devolve os ids abertos."""
        with self.tick_lock:
            self._finish()
            opened = []
            while True:
                if self.prepare:
                    self.prepare()
                request = self.queue.next_admissible(self.limits)
                if request is None:
                    return opened
                try:
                    state = self.open(request)
                except NoClient as e:
                    self.errors[request['id']] = str(e)
                    return opened  # fica na fila, e a próxima volta tenta de novo
                except CycleError as e:  # o CLI respondeu e falhou: o pedido falhou, e o teto não se ocupa
                    self.errors[request['id']] = str(e)
                    self.log(f'pedido {request["id"]}: {e}')
                    self.queue.mark_done(request['id'], failed=True)
                    continue
                self.queue.mark_running(request['id'], session=json.dumps(state))
                opened.append(request['id'])

    def _finish(self):
        rows = self.queue._all("SELECT request_id, session FROM workers WHERE state = 'running' AND session IS NOT NULL")
        for row in rows:
            try:
                state = json.loads(row['session'])
                request = self.queue.get(row['request_id'])
                outcome = self.poll(request, state)
            except (ValueError, KeyError, CycleError) as e:
                self.errors[row['request_id']] = str(e)
                continue
            if outcome:
                self.queue.mark_done(row['request_id'], failed=outcome == 'failed')

    # ---- a thread ----

    def kick(self):
        self.wake.set()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # a thread não morre: o erro vai ao log e a próxima volta tenta
                self.log(f'ciclo: {e}')
            self.wake.wait(self.interval)
            self.wake.clear()

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='brza-mcp-cycle', daemon=True)
        self.thread.start()

    def stop(self, timeout=5.0):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout)
