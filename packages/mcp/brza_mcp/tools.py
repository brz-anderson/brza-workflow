"""As ferramentas MCP do orquestrador (D3): `dispatch`, `status` e `config`, e os gates da R5.2.

    tools.install(REGISTRY)  # o `serve` chama; os testes passam a fila, os tetos e o leitor do índice trocados

Só `role: "consultant"` sem `account` entra direto na fila. Qualquer outro papel, ou a troca de conta, devolve
`{"gate": {...}}` com a proposta e grava o pedido `gated`; a sessão principal pergunta a Anderson e chama `dispatch`
de novo com `approved: <id do gate>`, pelo mesmo chamador. O chamador é o `X-Brza-Session` (H1) e, sem o header, o
argumento `caller`. As sobreposições da política ficam na tabela `config` do SQLite da fila; o `Policy` (P4) as
funde por cima do `brza.toml`.
"""
import json
import os
import secrets
from datetime import date

from . import limits as limits_mod
from . import policy as policy_mod
from . import queue as queue_mod

ROLE_CONSULTANT = 'consultant'
RUNTIMES = ('claude',)
CLIENTS = ('studio', 'orca')  # o header X-Brza-Client

_STRINGS = ('task', 'role', 'target', 'model', 'effort', 'account', 'ref', 'caller', 'approved')
_ARGUMENTS = _STRINGS + ('runtime', 'deadline', 'blocks', 'priority')

DISPATCH_SCHEMA = {'type': 'object', 'required': ['task', 'role'], 'properties': {
    'task': {'type': 'string'}, 'role': {'type': 'string'}, 'target': {'type': 'string'},
    'runtime': {'enum': list(RUNTIMES)}, 'model': {'type': 'string'}, 'effort': {'type': 'string'},
    'account': {'type': 'string'}, 'ref': {'type': 'string'}, 'deadline': {'type': 'string', 'format': 'date'},
    'blocks': {'type': 'array', 'items': {'type': 'string'}}, 'priority': {'enum': list(queue_mod.PRIORITIES)},
    'caller': {'type': 'string'}, 'approved': {'type': 'string'}}}
STATUS_SCHEMA = {'type': 'object', 'properties': {}}
CONFIG_SCHEMA = {'type': 'object', 'properties': {'key': {'type': 'string'}, 'value': {}}}

# as chaves de sobreposição são as do `brza.toml` (D4); o tipo do valor é o que o leitor do P4 espera
_BOOL_KEYS = ('policy.enforce', 'policy.suggest_to_main')
_NUMBER_KEYS = ('orchestrator.max_workers',)
_PREFIXES = ('quota.', 'machine.', 'resolver.')
_CEILING_KEYS = ('orchestrator.max_workers', 'quota.max_sessions_per_account', 'quota.hard_cap_percent',
                 'machine.min_free_mem_gb', 'machine.max_swap_gb', 'machine.max_cpu_percent')

GATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS gates (
    id TEXT PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id), caller TEXT NOT NULL,
    reason TEXT NOT NULL, created_at REAL NOT NULL, approved_at REAL
);
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ToolError(Exception):
    """Erro de argumento: vira resultado de ferramenta com `isError`, e o servidor segue."""


def _error(message):
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---- as sobreposições da política ----

def check_key(key):
    if not (key in _BOOL_KEYS or key in _NUMBER_KEYS or any(key.startswith(p) and len(key) > len(p)
                                                            for p in _PREFIXES)):
        raise ToolError(f'chave de configuração desconhecida: {key!r} (policy.enforce, policy.suggest_to_main, '
                        'orchestrator.max_workers, quota.*, machine.*, resolver.*)')


def check_config(key, value):
    check_key(key)
    if key in _BOOL_KEYS:
        if not isinstance(value, bool):
            raise ToolError(f'{key} é booleano, veio {json.dumps(value)}')
    elif key in _NUMBER_KEYS or key.startswith(('quota.', 'machine.')):
        if not _is_number(value):
            raise ToolError(f'{key} é numérico, veio {json.dumps(value)}')
    elif isinstance(value, (dict, list)):
        raise ToolError(f'{key} é escalar, veio {json.dumps(value)}')


def get_overrides(queue) -> dict:
    """As sobreposições gravadas, chave plana do `brza.toml` -> valor. O P4 as funde por cima do TOML."""
    return {r['key']: json.loads(r['value']) for r in queue._all('SELECT key, value FROM config ORDER BY key')}


class Orchestrator:
    def __init__(self, queue, limits=None, workspace=None, index_reader=queue_mod.git_index_reader, policy=None,
                 resolver=None, on_queued=None):
        self.queue = queue
        self.on_queued = on_queued  # o `Scheduler.kick` (P5): acorda o ciclo quando um pedido entra na fila
        self.limits = limits or limits_mod.Limits()
        self.base_config = dict(self.limits.config)
        self.workspace = workspace if workspace is not None else os.environ.get('BRZA_WORKSPACE')
        self.policy = policy or policy_mod.Policy(self.workspace, overrides=self.get_overrides)
        self.resolver = resolver
        self.index_reader = index_reader
        with queue.lock:
            queue.conn.executescript(GATES_SCHEMA)
            queue.conn.commit()
        self.sync_limits()

    def get_overrides(self) -> dict:
        return get_overrides(self.queue)

    def sync_limits(self):
        # o `brza.toml` (ou os padrões do D4) por baixo, o que o `Limits` recebeu em cima dele, e as sobreposições
        self.limits.config = {**self.policy.load(), **self.base_config, **self.get_overrides()}

    def _index(self, ref):
        if not self.workspace:
            return {}
        return queue_mod.index_fields(ref, self.workspace, self.index_reader)

    # ---- dispatch ----

    def validate(self, args) -> dict:
        unknown = sorted(set(args) - set(_ARGUMENTS))
        if unknown:
            raise ToolError(f'argumento desconhecido: {", ".join(unknown)}')
        for name in ('task', 'role'):
            if not isinstance(args.get(name), str) or not args[name].strip():
                raise ToolError(f'{name} é obrigatório e é texto não vazio')
        for name in _STRINGS:
            if name in args and not isinstance(args[name], str):
                raise ToolError(f'{name} deve ser texto, veio {json.dumps(args[name])}')
        if 'runtime' in args and args['runtime'] not in RUNTIMES:
            raise ToolError(f'runtime {args["runtime"]!r} não é aceito: só "claude" (o M4 é fase 3)')
        if 'priority' in args and args['priority'] not in queue_mod.PRIORITIES:
            raise ToolError(f'priority deve ser high, medium ou low, veio {json.dumps(args["priority"])}')
        if 'deadline' in args:
            try:
                date.fromisoformat(args['deadline'])
            except (TypeError, ValueError):
                raise ToolError(f'deadline deve ser data ISO AAAA-MM-DD, veio {json.dumps(args["deadline"])}') from None
        if 'blocks' in args and not (isinstance(args['blocks'], list)
                                     and all(isinstance(b, str) for b in args['blocks'])):
            raise ToolError('blocks deve ser lista de textos')
        return args

    def dispatch(self, args, ctx):
        try:
            args = self.validate(args)
            caller = ctx.session or args.get('caller')
            if not caller:
                raise ToolError('chamador desconhecido: o header X-Brza-Session não veio e o argumento caller falta')
            if 'approved' in args:
                return self.approve(args['approved'], caller)
            fields = {k: args[k] for k in ('task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref',
                                           'deadline', 'blocks', 'priority') if k in args}
            decided = self.policy.decide_model(fields, self.resolver)
            fields.update({k: decided[k] for k in ('model', 'effort') if decided[k] is not None})
            client = next((v for k, v in (ctx.headers or {}).items() if k.lower() == 'x-brza-client'), None)
            if client in CLIENTS:
                fields['client'] = client
            reason = self.gate_reason(args)
            if reason is None:
                return self.queued(self.queue.enqueue(**fields, caller=caller, index=self._index))
            request = self.queue.enqueue(**fields, caller=caller, state='gated', index=self._index)
            gate_id = 'gate-' + secrets.token_hex(8)
            self.queue._run('INSERT INTO gates (id, request_id, caller, reason, created_at) VALUES (?,?,?,?,?)',
                            (gate_id, request, caller, reason, self.queue.get(request)['created_at']))
            proposal = {k: args.get(k) for k in ('task', 'role', 'account') if k in args}
            proposal.update({k: args[k] for k in ('target', 'model', 'effort', 'ref') if k in args})
            return {'gate': {'id': gate_id, 'reason': reason, 'proposal': proposal}}
        except (ToolError, ValueError) as e:
            return _error(str(e))

    @staticmethod
    def gate_reason(args):
        if args['role'] != ROLE_CONSULTANT:
            return (f'o papel {args["role"]!r} não é consultant: abrir executor ou outro papel exige a '
                    'confirmação de quem pediu (R5.2)')
        if args.get('account') is not None:
            return f'troca de conta ({args["account"]!r}) exige a confirmação de quem pediu (R5.2)'
        return None

    def approve(self, gate_id, caller):
        rows = self.queue._all('SELECT * FROM gates WHERE id = ?', (gate_id,))
        if not rows:
            raise ToolError(f'gate desconhecido: {gate_id!r}')
        gate = rows[0]
        if gate['caller'] != caller:
            raise ToolError(f'o gate {gate_id} foi aberto por outro chamador; só quem o abriu o aprova')
        request = self.queue.get(gate['request_id'])
        if gate['approved_at'] is not None or request['state'] != 'gated':
            raise ToolError(f'o gate {gate_id} já foi usado')
        with self.queue.lock:
            self.queue.set_state(request['id'], 'queued')
            self.queue._run('UPDATE gates SET approved_at = ? WHERE id = ?', (self.queue.get(request['id'])['updated_at'],
                                                                              gate_id))
        return self.queued(request['id'])

    def queued(self, request_id):
        """A resposta do pedido na fila: a posição na ordem da R9.2 e o teto que o segura, se algum."""
        self.sync_limits()
        with self.queue.lock:
            self.queue.next_admissible(self.limits)  # grava o teto de cada pedido que espera
            pending = self.queue.pending()
        if self.on_queued:
            self.on_queued()
        position = next(i for i, r in enumerate(pending, 1) if r['id'] == request_id)
        row = pending[position - 1]
        return {'request': request_id, 'state': 'queued', 'position': position, 'held_by': row['held_by'],
                'held_reason': row['held_reason']}

    # ---- status ----

    def status(self, args, ctx):
        self.sync_limits()
        with self.queue.lock:
            self.queue.next_admissible(self.limits)
            pending = self.queue.pending()
            gated = [queue_mod._row(r) for r in self.queue._all("SELECT * FROM requests WHERE state = 'gated' ORDER BY id")]
            running = [queue_mod._row(r) for r in self.queue._all("SELECT * FROM requests WHERE state = 'running' ORDER BY id")]

        def brief(r, position=None):
            out = {k: r[k] for k in ('id', 'task', 'role', 'account', 'ref', 'caller')}
            if position is not None:
                out.update({'position': position, 'held_by': r['held_by'], 'held_reason': r['held_reason']})
            return out
        ceilings = {key: self.limits.get(key) for key in _CEILING_KEYS}
        if ceilings['quota.hard_cap_percent'] is None:
            ceilings['quota.hard_cap_percent'] = limits_mod.DEFAULT_HARD_CAP
        return {'queued': [brief(r, i) for i, r in enumerate(pending, 1)], 'gated': [brief(r) for r in gated],
                'running': [brief(r) for r in running], 'ceilings': ceilings, 'suggestion': self.policy.suggestion()}

    # ---- config ----

    def config(self, args, ctx):
        try:
            unknown = sorted(set(args) - {'key', 'value'})
            if unknown:
                raise ToolError(f'argumento desconhecido: {", ".join(unknown)}')
            if 'key' not in args:
                if 'value' in args:
                    raise ToolError('value sem key')
                return {'overrides': self.get_overrides()}
            key = args['key']
            if not isinstance(key, str):
                raise ToolError('key deve ser texto')
            if 'value' not in args:
                check_key(key)
                return {'key': key, 'value': self.get_overrides().get(key)}
            if args['value'] is None:  # null apaga a sobreposição
                check_key(key)
                self.queue._run('DELETE FROM config WHERE key = ?', (key,))
            else:
                check_config(key, args['value'])
                self.queue._run('INSERT INTO config (key, value) VALUES (?, ?)'
                                ' ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                                (key, json.dumps(args['value'])))
            self.sync_limits()
            return {'key': key, 'value': args['value']}
        except ToolError as e:
            return _error(str(e))


def install(registry, queue=None, limits=None, workspace=None, index_reader=queue_mod.git_index_reader, policy=None,
            resolver=None, on_queued=None):
    """Registra as três ferramentas no `registry`; sem `queue`, abre a fila do BRZA_HOME. Devolve o serviço."""
    service = Orchestrator(queue if queue is not None else queue_mod.Queue(), limits, workspace, index_reader, policy,
                           resolver, on_queued)
    registry.register('dispatch', 'Pede um worker: o consultor entra na fila; outro papel ou troca de conta devolve '
                      'um gate, que se aprova chamando de novo com approved.', service.dispatch, DISPATCH_SCHEMA)
    registry.register('status', 'A fila, os gates, os workers abertos e os tetos vigentes.', service.status,
                      STATUS_SCHEMA)
    registry.register('config', 'Lê ou grava as sobreposições da política (chaves do brza.toml).', service.config,
                      CONFIG_SCHEMA)
    return service
