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
POLL_FAILURES = 3  # as consultas seguidas que falham antes de perguntar ao cliente se o worker ainda existe


class CycleError(RuntimeError):
    """`code` e `body` vêm do erro que o CLI respondeu (`{ok: false, error: {code}}`); sem eles, o CLI nem respondeu."""

    def __init__(self, message, code=None, body=None):
        super().__init__(message)
        self.code, self.body = code, body


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
                 interval=DEFAULT_INTERVAL, log=None, telemetry=None):
        self.queue, self.limits, self.run, self.detect = queue, limits, run, detect
        self.prepare = prepare  # o `sync_limits` do serviço: refaz a configuração antes de cada decisão
        self.interval = interval
        self.log = log or (lambda message: None)
        self.telemetry = telemetry  # a linha `route` do fim do worker (P6)
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        self.tick_lock = threading.Lock()
        self.errors = {}
        self.poll_failures = {}

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
        argv = [clients_mod.cli_path(client), 'orchestration', *args, '--json']
        code, out, err = self.run(argv, env, 120)
        try:
            body = json.loads(out)
        except ValueError:
            body = None
        if code != 0 or not isinstance(body, dict) or body.get('ok') is False:
            detail = json.dumps(body.get('error', body)) if isinstance(body, dict) else (err.strip() or out.strip())
            error = body.get('error') if isinstance(body, dict) else None
            raise CycleError(f'orchestration {args[0]} falhou: {detail}',
                             code=error.get('code') if isinstance(error, dict) else None,
                             body=body if isinstance(body, dict) else None)
        return body.get('result', body)

    # ---- abrir ----

    def open(self, request, save=None):
        """run-create, task-create e worker-start; devolve o estado do worker, que vai à coluna `session`. `save(estado)`
        grava o que já existe antes do `worker-start`, para uma queda no meio não deixar run e task sem dono."""
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
        state = {'client': client, 'run': run, 'task': task, 'dispatch': None}
        if save:
            save(dict(state))
        args = ['worker-start', '--task', task, '--run', run, '--agent', AGENT]
        if request.get('model'):
            args += ['--model', request['model']]
            if request.get('effort'):  # o CLI exige --model com --effort
                args += ['--effort', request['effort']]
        try:
            started = self.cli(client, env, *args)
        except CycleError as e:
            dispatch = find_key(e.body, 'dispatchId', 'dispatch_id') if e.body else None
            if e.code == 'start_outcome_unknown':  # o worker pode estar de pé: o poll decide, e ninguém o relança
                state['dispatch'] = dispatch
                return state
            self._abandon(client, env, task, dispatch)
            raise
        state['dispatch'] = find_key(started, 'dispatchId', 'dispatch_id')
        return state

    def _abandon(self, client, env, task, dispatch):
        """O `worker-start` falhou de vez: o que ele deixou se libera e a task fecha como `failed`, sem pendurar."""
        steps = ([('worker-release', '--dispatch', dispatch)] if dispatch else []) + [
            ('task-update', '--id', task, '--status', 'failed')]
        for step in steps:
            try:
                self.cli(client, env, *step)
            except CycleError as e:
                self.log(f'limpeza depois do worker-start: {e}')

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
            opened, skipped = [], set()
            while True:
                if self.prepare:
                    self.prepare()
                request = self.queue.admit_next(self.limits, exclude=skipped)  # a vaga já sai reservada
                if request is None:
                    return opened
                rid = request['id']
                try:
                    state = self.open(request, save=lambda s, rid=rid: self.queue.set_session(rid, json.dumps(s)))
                except NoClient as e:  # fica na fila, e o próximo admissível tenta: este não segura a fila
                    self.queue.release(rid)
                    skipped.add(rid)
                    if self.errors.get(rid) != str(e):
                        self.log(f'pedido {rid}: {e}')
                    self.errors[rid] = str(e)
                    self.queue.set_error(rid, str(e))
                    continue
                except CycleError as e:  # o CLI respondeu e falhou: o pedido falhou, e o teto não se ocupa
                    self.errors[rid] = str(e)
                    self.log(f'pedido {rid}: {e}')
                    self.queue.mark_done(rid, failed=True, error=str(e))
                    self._route('failed', rid, str(e))
                    continue
                self.queue.set_session(rid, json.dumps(state))
                self.queue.set_error(rid, None)
                self.errors.pop(rid, None)
                opened.append(rid)

    def _finish(self):
        rows = self.queue._all("SELECT request_id, session FROM workers WHERE state = 'running' AND session IS NOT NULL")
        for row in rows:
            rid, state = row['request_id'], None
            try:
                state = json.loads(row['session'])
                request = self.queue.get(rid)
                outcome = self.poll(request, state)
            except (ValueError, KeyError, CycleError) as e:
                self.errors[rid] = str(e)
                self.poll_failures[rid] = self.poll_failures.get(rid, 0) + 1
                if self.poll_failures[rid] >= POLL_FAILURES and self._gone(rid, state):
                    self.queue.mark_done(rid, failed=True, error=f'o worker sumiu do cliente (a consulta falhou '
                                                                 f'{self.poll_failures[rid]} vezes): {e}')
                    self._route('failed', rid, f'worker sumiu do cliente: {e}')
                    self.poll_failures.pop(rid, None)
                continue
            self.poll_failures.pop(rid, None)
            if outcome:
                self.queue.mark_done(rid, failed=outcome == 'failed',
                                     error='a task do worker terminou como failed' if outcome == 'failed' else None)
                self._route(outcome, rid, f'task {outcome} no worker')

    def _gone(self, request_id, state):
        """O cliente responde que não conhece o worker: só então ele se dá por perdido. Cliente fora do ar, ou sem o
        `dispatchId` para perguntar, mantém o pedido `running`."""
        dispatch = (state or {}).get('dispatch')
        if not dispatch:
            return False
        try:
            env = self.env_for(self.queue.get(request_id), state['client'])
            self.cli(state['client'], env, 'worker-show', '--dispatch', dispatch)
        except (CycleError, KeyError) as e:
            return isinstance(e, CycleError) and e.body is not None
        return False

    def _route(self, decision, request_id, reason):
        if self.telemetry:
            self.telemetry.emit(decision, self.queue.get(request_id), reason=reason)

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
        for request_id in self.queue.orphan_workers():  # a queda de antes veio entre a reserva e o cliente
            self.queue.release(request_id)
        self.thread = threading.Thread(target=self._loop, name='brza-mcp-cycle', daemon=True)
        self.thread.start()

    def stop(self, timeout=5.0):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout)
