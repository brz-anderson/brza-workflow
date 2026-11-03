"""As ferramentas MCP do orquestrador (D3): `dispatch`, `status` e `config`, e os gates da R5.2.

    tools.install(REGISTRY)  # o `serve` chama; os testes passam a fila, os tetos e o leitor do índice trocados

Só `role: "consultant"` sem `account` entra direto na fila. Qualquer outro papel, ou a troca de conta, devolve
`{"gate": {...}}` com a proposta e grava o pedido `gated`; a sessão principal pergunta a Anderson e chama `dispatch`
de novo com `approved: <id do gate>`, pelo mesmo chamador. O chamador é o `X-Brza-Session` (H1) e, sem o header, o
argumento `caller`. As sobreposições da política ficam na tabela `config` do SQLite da fila; o `Policy` (P4) as
funde por cima do `brza.toml`.
"""
import json
import math
import os
import secrets
import time

from . import accounts as accounts_mod
from . import clients as clients_mod
from . import limits as limits_mod
from . import policy as policy_mod
from . import queue as queue_mod
from .telemetry import Telemetry

ROLE_CONSULTANT = 'consultant'
RUNTIMES = ('claude',)

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

# as chaves de sobreposição são as do `brza.toml` (D4), em lista fechada: chave com erro de digitação se recusa em vez
# de gravar um valor que nada lê. Cada uma leva o tipo e a faixa: (tipo, mínimo, máximo, mínimo exclusivo)
_KEYS = {
    'policy.enforce': ('bool',),
    'policy.suggest_to_main': ('bool',),
    'orchestrator.max_workers': ('int', 0, None, False),
    'quota.soft_cap_percent': ('number', 0, 100, True),
    'quota.hard_cap_percent': ('number', 0, 100, True),
    'quota.max_sessions_per_account': ('int', 0, None, False),
    'machine.min_free_mem_gb': ('number', 0, None, False),
    'machine.max_swap_gb': ('number', 0, None, False),
    'machine.max_cpu_percent': ('number', 0, 100, True),
    'resolver.ollama_url': ('string',),
    'resolver.tev1_model': ('string',),
    'resolver.tev1_keep_alive': ('string',),
    'resolver.tev1_min_confidence': ('number', 0, 1, False),
    'resolver.haiku_max_budget_usd': ('number', 0, None, True),
}
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
    if key not in _KEYS:
        raise ToolError(f'chave de configuração desconhecida: {key!r} (as do brza.toml: {", ".join(sorted(_KEYS))})')


def check_config(key, value):
    check_key(key)
    kind, *bounds = _KEYS[key]
    shown = json.dumps(value)
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ToolError(f'{key} é booleano, veio {shown}')
    elif kind == 'string':
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f'{key} é texto não vazio, veio {shown}')
    else:
        if not _is_number(value) or not math.isfinite(value) or (kind == 'int' and not isinstance(value, int)):
            raise ToolError(f'{key} é {"inteiro" if kind == "int" else "numérico finito"}, veio {shown}')
        low, high, low_open = bounds
        if value < low or (low_open and value == low) or (high is not None and value > high):
            span = f'{"maior que" if low_open else "a partir de"} {low}' + (f' e até {high}' if high is not None else '')
            raise ToolError(f'{key} fica {span}, veio {shown}')


def get_overrides(queue) -> dict:
    """As sobreposições gravadas, chave plana do `brza.toml` -> valor. O P4 as funde por cima do TOML."""
    return {r['key']: json.loads(r['value']) for r in queue._all('SELECT key, value FROM config ORDER BY key')}


class Orchestrator:
    def __init__(self, queue, limits=None, workspace=None, index_reader=queue_mod.git_index_reader, policy=None,
                 resolver=None, on_queued=None, telemetry=None, accounts=None):
        self.queue = queue
        self.accounts = accounts  # o leitor das contas do cliente (`Accounts`); sem ele, o `account` vale como veio
        self.on_queued = on_queued  # o `Scheduler.kick` (P5): acorda o ciclo quando um pedido entra na fila
        self.limits = limits or limits_mod.Limits()
        self.base_config = dict(self.limits.config)
        self.workspace = workspace if workspace is not None else os.environ.get('BRZA_WORKSPACE')
        self.policy = policy or policy_mod.Policy(self.workspace, overrides=self.get_overrides)
        self.resolver = resolver
        self.policy_error = None
        self.telemetry = telemetry or Telemetry(self.workspace)
        queue.on_decision = self.telemetry.decided
        self.index_reader = index_reader
        with queue.lock:
            queue.conn.executescript(GATES_SCHEMA)
            queue.conn.commit()
        self.sync_limits()

    def get_overrides(self) -> dict:
        return get_overrides(self.queue)

    def sync_limits(self):
        # o `brza.toml` (ou os padrões do D4) por baixo, o que o `Limits` recebeu em cima dele, e as sobreposições;
        # com o `brza.toml` ilegível o servidor sobe e o ciclo segue com os padrões, e as ferramentas dizem o arquivo
        try:
            base, self.policy_error = self.policy.load(), None
        except policy_mod.PolicyError as e:
            base, self.policy_error = dict(policy_mod.DEFAULTS), str(e)
        self.limits.config = {**base, **self.base_config, **self.get_overrides()}

    def check_policy(self):
        """Cada ferramenta confere o `brza.toml` antes de agir: ilegível, ela responde `isError` com o caminho."""
        try:
            self.policy.load()
        except policy_mod.PolicyError as e:
            raise ToolError(str(e)) from None

    def _index(self, ref):
        if not self.workspace:
            return {}
        return queue_mod.index_fields(ref, self.workspace, self.index_reader)

    # ---- a conta ----

    def normalize_account(self, name):
        """O id da conta do pedido. O worker abre na conta ativa do cliente: o `worker-start` não tem flag de conta
        (X20), então outra conta é recusada com o motivo. Sem leitor de contas, vale como veio."""
        if self.accounts is None or not hasattr(self.accounts, 'find'):
            return name
        try:
            found = self.accounts.find(name)
            active = self.accounts.find(None)
        except accounts_mod.AccountError as e:
            raise ToolError(f'não consegui ler as contas do cliente para conferir {name!r}: {e}') from None
        if found is None:
            raise ToolError(f'conta desconhecida: {name!r}')
        if active is not None and found['id'] != active['id']:
            raise ToolError(f'o worker abre na conta ativa do cliente ({active["label"]}), e o `worker-start` não tem '
                            f'flag de conta: {found["label"]} só vale se for a ativa; troque a conta ativa no app ou '
                            'tire o account do pedido')
        return found['id']

    def active_account(self):
        return self.accounts.active_id() if hasattr(self.accounts, 'active_id') else None

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
                queue_mod.normalize_deadline(args['deadline'])
            except ValueError:
                raise ToolError(f'deadline deve ser data ISO AAAA-MM-DD, veio {json.dumps(args["deadline"])}') from None
        if 'blocks' in args and not (isinstance(args['blocks'], list)
                                     and all(isinstance(b, str) for b in args['blocks'])):
            raise ToolError('blocks deve ser lista de textos')
        return args

    def dispatch(self, args, ctx):
        try:
            self.check_policy()
            args = self.validate(args)
            caller = (ctx.session or '').strip() or (args.get('caller') or '').strip()
            if not caller:
                raise ToolError('chamador desconhecido: o header X-Brza-Session não veio e o argumento caller falta')
            if 'approved' in args:
                return self.approve(args['approved'], caller, args)
            fields = {k: args[k] for k in ('task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref',
                                           'deadline', 'blocks', 'priority') if k in args}
            fields.setdefault('runtime', 'claude')  # o teto de sessões conta por runtime: pedido sem ele escaparia
            if 'account' in fields:  # a mesma conta sob id, e-mail ou rótulo é uma conta só
                fields['account'] = self.normalize_account(fields['account'])
            elif self.active_account():
                fields['account'] = self.active_account()  # o pedido sem `account` conta na ativa
            decided = self.policy.decide_model(fields, self.resolver)
            fields.update({k: decided[k] for k in ('model', 'effort') if decided[k] is not None})
            client = clients_mod.from_header(
                next((v for k, v in (ctx.headers or {}).items() if k.lower() == 'x-brza-client'), None))
            if client:
                fields['client'] = client
            reason = self.gate_reason(args)
            if reason is None:
                request = self.queue.enqueue(**fields, caller=caller, index=self._index)
                self.telemetry.policy(self.queue.get(request), decided)
                self.telemetry.emit('queued', self.queue.get(request), reason=decided['reason'])
                row = self.queue.get(request)
                return {**self.queued(request), 'model': row['model'], 'effort': row['effort'],
                        'reason': decided['reason']}
            gate_id = 'gate-' + secrets.token_hex(8)
            with self.queue.lock:  # o pedido `gated` e o gate nascem juntos: sem o gate, o pedido ficaria sem saída
                request = self.queue.enqueue(**fields, caller=caller, state='gated', index=self._index)
                try:
                    self.queue._run('INSERT INTO gates (id, request_id, caller, reason, created_at) VALUES (?,?,?,?,?)',
                                    (gate_id, request, caller, reason, self.queue.get(request)['created_at']))
                except Exception:
                    self.queue._run('DELETE FROM requests WHERE id = ?', (request,))
                    raise
            self.telemetry.policy(self.queue.get(request), decided)
            self.telemetry.emit('gated', self.queue.get(request), reason=reason, gate=gate_id)
            # a proposta é o que o pedido guardado vai ser, depois da política: o que Anderson aprova é o que roda
            proposal = {k: fields[k] for k in ('task', 'role', 'target', 'model', 'effort', 'account', 'ref')
                        if k in fields}
            proposal['reason'] = decided['reason']
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

    def approve(self, gate_id, caller, args=None):
        args = args or {}
        with self.queue.lock:  # a leitura, a conferência e a gravação são um passo só: duas aprovações não passam juntas
            rows = self.queue._all('SELECT * FROM gates WHERE id = ?', (gate_id,))
            if not rows:
                raise ToolError(f'gate desconhecido: {gate_id!r}')
            gate = rows[0]
            if gate['caller'] != caller:
                raise ToolError(f'o gate {gate_id} foi aberto por outro chamador; só quem o abriu o aprova')
            request = self.queue.get(gate['request_id'])
            if gate['approved_at'] is not None or request['state'] != 'gated':
                raise ToolError(f'o gate {gate_id} já foi usado')
            for name in ('task', 'role', 'account'):
                given = args.get(name)
                if given is not None and name == 'account':
                    given = self.normalize_account(given)
                if given is not None and given != request[name]:
                    raise ToolError(f'o gate {gate_id} foi aberto para {name} {request[name]!r}: a aprovação é da '
                                    f'proposta guardada, e veio {args[name]!r}; abra outra proposta')
            now = time.time()
            try:
                won = self.queue.conn.execute('UPDATE gates SET approved_at = ? WHERE id = ? AND approved_at IS NULL',
                                              (now, gate_id)).rowcount
                moved = self.queue.conn.execute("UPDATE requests SET state = 'queued', updated_at = ? WHERE id = ?"
                                                " AND state = 'gated'", (now, request['id'])).rowcount
                if (won, moved) != (1, 1):
                    raise ToolError(f'o gate {gate_id} já foi usado')
                self.queue.conn.commit()
            except BaseException:
                self.queue.conn.rollback()
                raise
        self.telemetry.emit('approved', self.queue.get(request['id']), reason=gate['reason'], gate=gate_id)
        self.telemetry.emit('queued', self.queue.get(request['id']), reason='gate aprovado')
        row = self.queue.get(request['id'])
        return {**self.queued(request['id']), 'model': row['model'], 'effort': row['effort']}

    def queued(self, request_id):
        """A resposta do pedido na fila: a posição na ordem da R9.2 e o teto que o segura, se algum."""
        self.sync_limits()
        with self.queue.lock:
            self.queue.next_admissible(self.limits)  # grava o teto de cada pedido que espera
            pending = self.queue.pending()
        if self.on_queued:
            self.on_queued()
        position = next((i for i, r in enumerate(pending, 1) if r['id'] == request_id), None)
        if position is None:  # o ciclo já o abriu (ou o fechou): vale o estado real
            row = self.queue.get(request_id)
            return {'request': request_id, 'state': row['state'], 'position': None, 'held_by': None,
                    'held_reason': None}
        row = pending[position - 1]
        return {'request': request_id, 'state': 'queued', 'position': position, 'held_by': row['held_by'],
                'held_reason': row['held_reason']}

    # ---- status ----

    def status(self, args, ctx):
        try:
            self.check_policy()
        except ToolError as e:
            return _error(str(e))
        self.sync_limits()
        with self.queue.lock:
            self.queue.next_admissible(self.limits)
            pending = self.queue.pending()
            gated = [queue_mod._row(r) for r in self.queue._all(
                "SELECT r.*, g.id AS gate_id, g.reason AS reason FROM requests r LEFT JOIN gates g ON g.request_id = r.id"
                " AND g.approved_at IS NULL WHERE r.state = 'gated' ORDER BY r.id")]
            running = [queue_mod._row(r) for r in self.queue._all("SELECT * FROM requests WHERE state = 'running' ORDER BY id")]
            finished = self.queue.recent_finished(ctx.session) if getattr(ctx, 'session', None) else []

        def brief(r, position=None):
            out = {k: r[k] for k in ('id', 'task', 'role', 'account', 'ref', 'caller')}
            if position is not None:
                out.update({'position': position, 'held_by': r['held_by'], 'held_reason': r['held_reason']})
            if r.get('error'):
                out['error'] = r['error']
            if 'gate_id' in r.keys():
                out.update({'gate_id': r['gate_id'], 'reason': r['reason']})
            return out
        ceilings = {key: self.limits.get(key) for key in _CEILING_KEYS}
        ceilings['quota.hard_cap_percent'] = self.limits.hard_cap()
        return {'queued': [brief(r, i) for i, r in enumerate(pending, 1)], 'gated': [brief(r) for r in gated],
                'running': [brief(r) for r in running], 'finished': [dict(brief(r), state=r['state']) for r in finished],
                'ceilings': ceilings, 'suggestion': self.policy.suggestion()}

    # ---- config ----

    def config(self, args, ctx):
        try:
            self.check_policy()
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
            resolver=None, on_queued=None, telemetry=None, accounts=None):
    """Registra as três ferramentas no `registry`; sem `queue`, abre a fila do BRZA_HOME. Devolve o serviço."""
    service = Orchestrator(queue if queue is not None else queue_mod.Queue(), limits, workspace, index_reader, policy,
                           resolver, on_queued, telemetry, accounts)
    registry.register('dispatch', 'Pede um worker: o consultor entra na fila; outro papel ou troca de conta devolve '
                      'um gate, que se aprova chamando de novo com approved.', service.dispatch, DISPATCH_SCHEMA)
    registry.register('status', 'A fila, os gates, os workers abertos e os tetos vigentes.', service.status,
                      STATUS_SCHEMA)
    registry.register('config', 'Lê ou grava as sobreposições da política (chaves do brza.toml).', service.config,
                      CONFIG_SCHEMA)
    return service
