"""A fila do orquestrador: SQLite da stdlib em <BRZA_HOME>/orchestrator.db, as tabelas `requests`, `workers` e
`usage_samples`, a ordem da R9.2 e o índice da `master` (de onde vêm `deadline`, `blocks` e `priority` que o
`dispatch` não trouxe).

Uma conexão só, com `check_same_thread=False` e um RLock: o servidor é um ThreadingHTTPServer e cada requisição roda
numa thread. Os tetos que decidem a admissão são do `limits.py`; aqui só se percorre a fila.
"""
import contextlib
import json
import re
import sqlite3
import subprocess
import threading
import time
from datetime import date
from pathlib import Path

from . import paths

STATES = ('queued', 'gated', 'running', 'done', 'failed', 'cancelled')
PRIORITIES = ('high', 'medium', 'low')
_PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
_FIELDS = ('task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref', 'deadline', 'blocks',
           'priority', 'caller', 'client')

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL, role TEXT NOT NULL, target TEXT, runtime TEXT, model TEXT, effort TEXT, account TEXT,
    ref TEXT, deadline TEXT, blocks TEXT NOT NULL DEFAULT '[]', priority TEXT, caller TEXT, client TEXT,
    state TEXT NOT NULL DEFAULT 'queued', held_by TEXT, held_reason TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    account TEXT NOT NULL DEFAULT '', runtime TEXT NOT NULL DEFAULT '', session TEXT,
    counts_as_worker INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS usage_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL, runtime TEXT NOT NULL, window TEXT NOT NULL,
    used_percent REAL NOT NULL, resets_at INTEGER, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_by_window ON usage_samples (account, runtime, window, resets_at, ts);
"""


def default_db_path() -> Path:
    return paths.brza_home() / 'orchestrator.db'


_DEADLINE = re.compile(r'\d{4}-\d{2}-\d{2}')


def normalize_deadline(deadline) -> str:
    """A data `AAAA-MM-DD` e só ela: a fila ordena o `deadline` como texto, e `20261201` ou `2026-W49-2`, que o
    `date.fromisoformat` aceita, ordenariam errado."""
    try:
        if not (isinstance(deadline, str) and _DEADLINE.fullmatch(deadline)):
            raise ValueError
        return date.fromisoformat(deadline).isoformat()
    except ValueError:
        raise ValueError(f'deadline inválido: {deadline!r} (data ISO, AAAA-MM-DD)') from None


def _check_values(priority, deadline):
    if priority is not None and priority not in PRIORITIES:
        raise ValueError(f'priority inválida: {priority!r} (high, medium ou low)')
    return normalize_deadline(deadline) if deadline is not None else None


def _bucket(resets_at):
    """O `resetsAt` ao minuto mais próximo: o cliente o devolve com milissegundos que mudam a cada leitura."""
    return None if resets_at is None else round(resets_at / 60_000) * 60_000


def _row(r):
    d = dict(r)
    d['blocks'] = json.loads(d['blocks'] or '[]')
    return d


def _order_key(request):
    """R9.2: `deadline` mais próximo; depois quem tem `blocks`; depois `priority`; depois a chegada."""
    return (request['deadline'] or '9999-12-31', 0 if request['blocks'] else 1,
            _PRIORITY_RANK.get(request['priority'], len(PRIORITIES)), request['id'])


class Queue:
    def __init__(self, db_path=None):
        self.db_path = str(db_path if db_path is not None else default_db_path())
        if self.db_path != ':memory:':
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.on_decision = None  # `(request, decision, chosen)`: a telemetria `route` (P6); nunca levanta
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(requests)')}
            for name in ('client', 'error'):  # o banco do P2 a P4 não os tem
                if name not in columns:
                    self.conn.execute(f'ALTER TABLE requests ADD COLUMN {name} TEXT')
            self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    def _run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    # ---- pedidos ----

    def enqueue(self, *, task, role, target=None, runtime=None, model=None, effort=None, account=None, ref=None,
                deadline=None, blocks=None, priority=None, caller=None, client=None, state='queued', index=None) -> int:
        """Grava o pedido. O que veio no `dispatch` vence o índice; `index(ref)` completa o que faltou."""
        if state not in STATES:
            raise ValueError(f'state inválido: {state!r}')
        if ref and index is not None and (deadline is None or blocks is None or priority is None):
            found = index(ref) or {}
            deadline = deadline if deadline is not None else found.get('deadline')
            blocks = blocks if blocks is not None else found.get('blocks')
            priority = priority if priority is not None else found.get('priority')
        deadline = _check_values(priority, deadline)
        now = time.time()
        cur = self._run(
            'INSERT INTO requests (task, role, target, runtime, model, effort, account, ref, deadline, blocks,'
            ' priority, caller, client, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (task, role, target, runtime, model, effort, account, ref, deadline, json.dumps(list(blocks or [])),
             priority, caller, client, state, now, now))
        return cur.lastrowid

    def get(self, request_id):
        rows = self._all('SELECT * FROM requests WHERE id = ?', (request_id,))
        return _row(rows[0]) if rows else None

    def set_state(self, request_id, state):
        if state not in STATES:
            raise ValueError(f'state inválido: {state!r}')
        self._run('UPDATE requests SET state = ?, updated_at = ? WHERE id = ?', (state, time.time(), request_id))

    def pending(self):
        """Os pedidos que esperam, na ordem da R9.2, cada um com o teto que o segura (`held_by`, `reason`)."""
        rows = sorted((_row(r) for r in self._all("SELECT * FROM requests WHERE state = 'queued'")), key=_order_key)
        return [{**r, 'reason': r['held_reason']} for r in rows]

    def next_admissible(self, limits):
        """Percorre a fila na ordem da R9.2 e devolve o primeiro que os tetos admitem; no resto grava o teto que
        segura, julgando-o com o escolhido já numa vaga. Não deixa rastro no escolhido: quem o abre chama
        `admit_next`, ou `mark_running` depois deste."""
        with self.lock:
            return self._admit(limits, reserve=False)

    def admit_next(self, limits, session=None, exclude=()):
        """Escolhe o primeiro pedido que os tetos admitem e o abre no mesmo lock: o pedido vira `running` e ganha o
        worker, então duas chamadas seguidas não admitem a mesma vaga duas vezes. `release` desfaz."""
        with self.lock:
            return self._admit(limits, reserve=True, session=session, exclude=exclude)

    def _admit(self, limits, reserve, session=None, exclude=()):
        scope = getattr(limits, 'scope', None)
        chosen, virtual = None, []
        try:
            with (scope() if scope else contextlib.nullcontext()):
                for request in self.pending():
                    if request['id'] in exclude:  # o ciclo já tentou abri-lo nesta volta
                        continue
                    decision = limits.decide(request, self)
                    first = decision.admit and chosen is None
                    if self.on_decision:
                        self.on_decision(request, decision, first)
                    if decision.admit:
                        held = (None, None)
                        if first:
                            chosen = request
                            self._open(request['id'], session, True)
                        else:
                            self._open(request['id'], None, True)  # cada admissível ocupa uma vaga para os de trás
                            virtual.append(request['id'])
                    else:
                        held = (decision.ceiling, decision.reason)
                    if held != (request['held_by'], request['held_reason']):
                        self._run('UPDATE requests SET held_by = ?, held_reason = ? WHERE id = ?', (*held, request['id']))
        finally:
            for request_id in virtual + ([chosen['id']] if chosen and not reserve else []):
                self.release(request_id)
        return self.get(chosen['id']) if chosen else None

    def _open(self, request_id, session, counts_as_worker):
        with self.lock:
            request = self.get(request_id)
            now = time.time()
            cur = self._run("UPDATE requests SET state = 'running', held_by = NULL, held_reason = NULL,"
                            " started_at = ?, updated_at = ? WHERE id = ? AND state = 'queued'", (now, now, request_id))
            if cur.rowcount != 1:
                raise ValueError(f'o pedido {request_id} não está queued: {request["state"] if request else "não existe"}')
            self._run('INSERT INTO workers (request_id, account, runtime, session, counts_as_worker, started_at)'
                      ' VALUES (?,?,?,?,?,?)', (request_id, request['account'] or '', request['runtime'] or '',
                                                session, 1 if counts_as_worker else 0, now))

    def release(self, request_id):
        """Desfaz a abertura de um pedido que não chegou a abrir (sem cliente, por exemplo): o pedido volta à fila e a
        vaga se livra."""
        with self.lock:
            self._run("DELETE FROM workers WHERE request_id = ? AND state = 'running'", (request_id,))
            self._run("UPDATE requests SET state = 'queued', started_at = NULL, updated_at = ? WHERE id = ?"
                      " AND state = 'running'", (time.time(), request_id))

    def set_error(self, request_id, error):
        """O motivo que o `status` mostra: de um pedido que espera sem poder abrir, ou de um que terminou mal."""
        self._run('UPDATE requests SET error = ? WHERE id = ?', (error, request_id))

    def recent_finished(self, caller, limit=10):
        """Os pedidos `done` e `failed` mais recentes do chamador, com o erro de quem falhou."""
        rows = self._all("SELECT * FROM requests WHERE caller = ? AND state IN ('done', 'failed')"
                         ' ORDER BY finished_at DESC, id DESC LIMIT ?', (caller, limit))
        return [_row(r) for r in rows]

    def orphan_workers(self):
        """Os workers reservados que não chegaram a ter sessão: a queda veio entre a reserva e o cliente."""
        return [r['request_id'] for r in self._all("SELECT request_id FROM workers WHERE state = 'running'"
                                                   ' AND counts_as_worker = 1 AND session IS NULL')]

    def set_session(self, request_id, session):
        """O estado do worker (a coluna `session`), depois que o cliente o abriu."""
        self._run("UPDATE workers SET session = ? WHERE request_id = ? AND state = 'running'", (session, request_id))

    def mark_running(self, request_id, session=None, counts_as_worker=True):
        """Abre o worker de um pedido `queued`. O desempatador entra com `counts_as_worker=False` (R2.3) e não pesa
        nos tetos. Pedido em outro estado levanta `ValueError`."""
        self._open(request_id, session, counts_as_worker)

    def mark_done(self, request_id, failed=False, error=None):
        """Fecha o worker e libera o teto: é o gancho do `worker_done` (P5). `error` é o motivo de quem falhou."""
        with self.lock:
            now = time.time()
            self._run('UPDATE requests SET state = ?, finished_at = ?, updated_at = ?, error = ? WHERE id = ?',
                      ('failed' if failed else 'done', now, now, error, request_id))
            self._run("UPDATE workers SET state = ?, finished_at = ? WHERE request_id = ? AND state = 'running'",
                      ('failed' if failed else 'done', now, request_id))

    def running_count(self, account=None, runtime=None):
        """Os workers abertos que contam nos tetos; com `account` e/ou `runtime`, só os que casam com o que veio."""
        sql, args = "SELECT COUNT(*) FROM workers WHERE state = 'running' AND counts_as_worker = 1", []
        if account is not None:
            sql += ' AND account = ?'
            args.append(account)
        if runtime is not None:
            sql += ' AND runtime = ?'
            args.append(runtime)
        return self._all(sql, args)[0][0]

    # ---- amostras de uso ----

    def record_usage(self, snapshot, ts_ms):
        """Guarda uma amostra por janela do instantâneo normalizado {account, runtime, windows: [...]} e apaga as
        amostras das janelas que já viraram (o `resetsAt` passou)."""
        with self.lock:
            self.conn.execute('DELETE FROM usage_samples WHERE resets_at IS NOT NULL AND resets_at < ?', (int(ts_ms),))
            for w in snapshot.get('windows') or []:
                if w.get('usedPercent') is None:
                    continue
                self.conn.execute(
                    'INSERT INTO usage_samples (account, runtime, window, used_percent, resets_at, ts)'
                    ' VALUES (?,?,?,?,?,?)', (snapshot.get('account') or '', snapshot.get('runtime') or '',
                                              w['name'], w['usedPercent'], _bucket(w.get('resetsAt')), int(ts_ms)))
            self.conn.commit()

    def samples(self, account, runtime, window, resets_at):
        """As amostras da janela com o mesmo `resetsAt` (ao minuto), da mais antiga à mais nova: [(ts_ms, usedPercent)]."""
        rows = self._all('SELECT ts, used_percent FROM usage_samples WHERE account = ? AND runtime = ?'
                         ' AND window = ? AND resets_at IS ? ORDER BY ts, id',
                         (account or '', runtime or '', window, _bucket(resets_at)))
        return [(r['ts'], r['used_percent']) for r in rows]


# ---- o índice da master ----

def git_index_reader(workspace) -> str:
    """O `.brza/index.jsonl` da `master`, sem checkout. Falha vira exceção, e `index_fields` a trata."""
    out = subprocess.run(['git', '-C', str(workspace), 'show', 'master:.brza/index.jsonl'],
                         capture_output=True, text=True, timeout=10, check=True)
    return out.stdout


def reduce_index(text) -> dict:
    """O log de eventos reduzido à última linha de cada `id`: {id: linha}. A linha que não é JSON é ignorada."""
    entries = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('id'):
            entries[entry['id']] = entry
    return entries


def index_fields(ref, workspace, reader=git_index_reader) -> dict:
    """`priority`, `blocks` e `deadline` do `ref` (o `id` do índice ou o `path`), só os que existem e são válidos.
    Sem `ref`, sem linha ou sem git, devolve {}."""
    if not ref:
        return {}
    try:
        entries = reduce_index(reader(workspace))
    except (OSError, subprocess.SubprocessError):
        return {}
    entry = entries.get(ref) or next((e for e in entries.values() if e.get('path') == ref), None)
    if not entry:
        return {}
    fields = {}
    if entry.get('priority') in PRIORITIES:
        fields['priority'] = entry['priority']
    blocks = entry.get('blocks')
    if isinstance(blocks, str):
        blocks = [b for b in re.split(r'[,\s]+', blocks) if b]
    if isinstance(blocks, list) and blocks:
        fields['blocks'] = [str(b) for b in blocks]
    try:
        if entry.get('deadline'):
            fields['deadline'] = normalize_deadline(entry['deadline'])
    except ValueError:
        pass
    return fields
