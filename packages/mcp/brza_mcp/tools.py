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
import sys
import time

from . import accounts as accounts_mod
from . import clients as clients_mod
from . import limits as limits_mod
from . import policy as policy_mod
from . import queue as queue_mod
from . import runtimes
from .telemetry import Telemetry

ROLE_CONSULTANT = 'consultant'

_STRINGS = ('task', 'role', 'target', 'model', 'effort', 'account', 'ref', 'caller', 'approved')
_ARGUMENTS = _STRINGS + ('runtime', 'deadline', 'blocks', 'priority')

DISPATCH_SCHEMA = {'type': 'object', 'required': ['task', 'role'], 'properties': {
    'task': {'type': 'string'}, 'role': {'type': 'string'}, 'target': {'type': 'string'},
    'runtime': {'enum': [*runtimes.RUNTIMES, *runtimes.ALIASES]}, 'model': {'type': 'string'}, 'effort': {'type': 'string'},
    'account': {'type': 'string'}, 'ref': {'type': 'string'}, 'deadline': {'type': 'string', 'format': 'date'},
    'blocks': {'type': 'array', 'items': {'type': 'string'}}, 'priority': {'enum': list(queue_mod.PRIORITIES)},
    'caller': {'type': 'string'}, 'approved': {'type': 'string'}}}
STATUS_SCHEMA = {'type': 'object', 'properties': {'caller': {'type': 'string'}}}
CONFIG_SCHEMA = {'type': 'object', 'properties': {'key': {'type': 'string'}, 'value': {}}}

_CEILING_KEYS = ('orchestrator.max_workers', 'quota.max_sessions_per_account', 'quota.hard_cap_percent',
                 'machine.min_free_mem_gb', 'machine.max_swap_gb', 'machine.max_cpu_percent')

GATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS gates (
    id TEXT PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id), caller TEXT NOT NULL,
    reason TEXT NOT NULL, created_at REAL NOT NULL, approved_at REAL, policy_reason TEXT
);
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ToolError(Exception):
    """Erro de argumento: vira resultado de ferramenta com `isError`, e o servidor segue."""


def _error(message):
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


# ---- as sobreposições da política ----

def check_key(key):
    if key not in policy_mod.KEYS:
        raise ToolError(f'chave de configuração desconhecida: {key!r} (as do brza.toml: '
                        f'{", ".join(sorted(policy_mod.KEYS))})')


def check_config(key, value):
    check_key(key)
    try:
        policy_mod.check_value(key, value)
    except ValueError as e:
        raise ToolError(str(e)) from None


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
        self._last_base = None  # a última configuração do `brza.toml` que se leu: o erro depois da subida não a solta
        self.telemetry = telemetry or Telemetry(self.workspace)
        queue.on_decision = self.telemetry.decided
        self.index_reader = index_reader
        with queue.lock:
            queue.conn.executescript(GATES_SCHEMA)
            if 'policy_reason' not in {r['name'] for r in queue.conn.execute('PRAGMA table_info(gates)')}:
                queue.conn.execute('ALTER TABLE gates ADD COLUMN policy_reason TEXT')  # o banco anterior ao motivo
            queue.conn.commit()
        self.sync_limits()

    def get_overrides(self) -> dict:
        return get_overrides(self.queue)

    def sync_limits(self):
        # o `brza.toml` (ou os padrões do D4) por baixo, o que o `Limits` recebeu em cima dele, e as sobreposições.
        # Com o `brza.toml` ilegível: na subida, sem leitura anterior, valem os padrões (X36); com o servidor de pé,
        # vale a última configuração lida, para o erro de edição não soltar o teto que Anderson apertou. As ferramentas
        # dizem o arquivo, e o erro vai ao stderr quando muda.
        try:
            base, error = self.policy.load(), None
            self._last_base = base
        except policy_mod.PolicyError as e:
            base, error = (self._last_base if self._last_base is not None else dict(policy_mod.DEFAULTS)), str(e)
        if error != self.policy_error and error is not None:
            kept = 'vale a última configuração lida' if self._last_base is not None else 'valem os padrões'
            print(f'brza-mcp: {error}; {kept}', file=sys.stderr, flush=True)
        self.policy_error = error
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

    def _for(self, client, user_data=None):
        """Os argumentos de cliente das chamadas ao leitor de contas: o do pedido, quando ele o trouxe."""
        return {key: value for key, value in (('client', client), ('user_data', user_data)) if value}

    def normalize_account(self, name, client=None, user_data=None, runtime='claude', expected=None):
        """O id da conta do pedido, lido no cliente do pedido. O worker abre na conta ativa do cliente: o `worker-start`
        não tem flag de conta (X20), então outra conta é recusada com o motivo. Fora do Claude, a conta gravada passa
        sem leitura da lista; qualquer outra é recusada com o motivo da E2. Sem leitor de contas, vale como veio."""
        if runtime != 'claude':
            if expected is None:
                expected = (self.accounts.active_id(runtime=runtime, **self._for(client, user_data))
                            if self.accounts and hasattr(self.accounts, 'active_id') else None) or accounts_mod.SYSTEM_DEFAULT
            if name == expected:
                return name
            raise ToolError('account fora do Claude entra na E2 do PLN-013: tire o account, e o worker abre na conta padrão '
                            'do runtime')
        if self.accounts is None or not hasattr(self.accounts, 'find'):
            return name
        try:
            found = self.accounts.find(name, **self._for(client, user_data))
            active = self.accounts.find(None, **self._for(client, user_data))
        except accounts_mod.AccountError as e:
            raise ToolError(f'não consegui ler as contas do cliente para conferir {name!r}: {e}') from None
        if found is None:
            raise ToolError(f'conta desconhecida: {name!r}')
        if active is not None and found['id'] != active['id']:
            raise ToolError(f'o worker abre na conta ativa do cliente ({active["label"]}), e o `worker-start` não tem '
                            f'flag de conta: {found["label"]} só vale se for a ativa; troque a conta ativa no app ou '
                            'tire o account do pedido')
        return found['id']

    def active_account(self, client=None, user_data=None):
        return self.accounts.active_id(**self._for(client, user_data)) if hasattr(self.accounts, 'active_id') else None

    def warm_accounts(self):
        """Aquece a leitura das contas, de cada cliente da fila, antes de qualquer lock: o `account list` leva até 30 s
        com o app travado, e o lock da fila segura o `status`, o `dispatch` e o ciclo."""
        warm = getattr(self.accounts, 'warm', None)
        if warm:
            for client, user_data in {(r.get('client'), r.get('user_data')) for r in self.queue.pending()} | {(None, None)}:
                warm(**self._for(client, user_data))

    def prepare(self):
        """O que o ciclo faz antes de cada decisão: as contas (fora do lock) e a configuração."""
        self.warm_accounts()
        self.sync_limits()

    @staticmethod
    def caller_of(ctx, args) -> str:
        """O chamador: o `X-Brza-Session` e, sem o header, o argumento `caller` (a mesma precedência em toda ferramenta)."""
        caller = args.get('caller')
        return (getattr(ctx, 'session', None) or '').strip() or (caller.strip() if isinstance(caller, str) else '')

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
        if 'runtime' in args:
            try:
                args = {**args, 'runtime': runtimes.get(args['runtime']).name}
            except KeyError:
                raise ToolError(f'runtime {args["runtime"]!r} não é aceito: claude, codex, grok ou antigravity (agy)') from None
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
            caller = self.caller_of(ctx, args)
            if not caller:
                raise ToolError('chamador desconhecido: o header X-Brza-Session não veio e o argumento caller falta')
            if 'approved' in args:
                return self.approve(args['approved'], caller, args)
            if args.get('runtime', 'claude') != 'claude' and 'account' in args:
                raise ToolError('account fora do Claude entra na E2 do PLN-013: tire o account, e o worker abre na conta padrão '
                                'do runtime')
            fields = {k: args[k] for k in ('task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref',
                                           'deadline', 'blocks', 'priority') if k in args}
            fields.setdefault('runtime', 'claude')  # o teto de sessões conta por runtime: pedido sem ele escaparia
            client, user_data = clients_mod.resolve_header(
                next((v for k, v in (ctx.headers or {}).items() if k.lower() == 'x-brza-client'), None))
            if client:
                fields['client'] = client
            if user_data:  # o perfil de outro build (brza-dev): o worker abre no runtime dele
                fields['user_data'] = user_data
            if fields['runtime'] != 'claude':
                fields['account'] = (self.accounts.active_id(runtime=fields['runtime'], **self._for(client, user_data))
                                     if self.accounts and hasattr(self.accounts, 'active_id') else None) or accounts_mod.SYSTEM_DEFAULT
            elif 'account' in fields:  # a mesma conta sob id, e-mail ou rótulo é uma conta só, lida no cliente do pedido
                fields['account'] = self.normalize_account(fields['account'], client, user_data)
            fields.setdefault('account', '')  # sem `account`: `''`, e o `Limits` o conta na ativa da hora da admissão
            decided = self.policy.decide_model(fields, self.resolver)
            for k in ('model', 'effort'):
                if decided.get(k) is not None:
                    fields[k] = decided[k]
                else:
                    fields.pop(k, None)
            reason = self.gate_reason(args, fields, client)
            if reason is None:
                request = self.queue.enqueue(**fields, caller=caller, index=self._index)
                self.telemetry.policy(self.queue.get(request), decided)
                self.telemetry.emit('queued', self.queue.get(request), reason=decided['reason'])
                row = self.queue.get(request)
                return {**self.queued(request), 'model': row['model'], 'effort': row['effort'],
                        'reason': decided['reason']}
            gate_id = 'gate-' + secrets.token_hex(8)
            found = self._index(fields.get('ref'))  # o `git show` fica fora do lock global da fila
            with self.queue.lock:  # o pedido `gated` e o gate nascem juntos: sem o gate, o pedido ficaria sem saída
                request = self.queue.enqueue(**fields, caller=caller, state='gated', index=lambda ref: found)
                try:
                    self.queue._run('INSERT INTO gates (id, request_id, caller, reason, created_at, policy_reason)'
                                    ' VALUES (?,?,?,?,?,?)',
                                    (gate_id, request, caller, reason, self.queue.get(request)['created_at'],
                                     decided['reason']))
                except Exception:
                    self.queue._run('DELETE FROM requests WHERE id = ?', (request,))
                    raise
            self.telemetry.policy(self.queue.get(request), decided)
            self.telemetry.emit('gated', self.queue.get(request), reason=reason, gate=gate_id)
            # a proposta é o que o pedido guardado vai ser, depois da política: o que Anderson aprova é o que roda
            proposal = {k: fields[k] for k in ('task', 'role', 'target', 'runtime', 'model', 'effort', 'account', 'ref')
                        if fields.get(k)}
            proposal['reason'] = decided['reason']
            return {'gate': {'id': gate_id, 'reason': reason, 'proposal': proposal}}
        except (ToolError, ValueError) as e:
            return _error(str(e))

    def gate_reason(self, args, fields, client=None):
        if args['role'] != ROLE_CONSULTANT:
            return (f'o papel {args["role"]!r} não é consultant: abrir executor ou outro papel exige a '
                    'confirmação de quem pediu (R5.2)')
        if args.get('account') is not None:
            # com o leitor de contas, `fields['account']` já é a conta normalizada, e a que não é a ativa foi recusada
            # (X38): só a troca de verdade, ou a que não se confere (sem leitor, ou sem ativa), pede a confirmação
            active = self.active_account(client, fields.get('user_data'))
            if active is None or fields.get('account') != active:
                return f'troca de conta ({args["account"]!r}) exige a confirmação de quem pediu (R5.2)'
        return None

    def approve(self, gate_id, caller, args=None):
        args = args or {}
        account = args.get('account')
        if account is not None:
            gate = self.queue._all('SELECT request_id FROM gates WHERE id = ?', (gate_id,))
            stored = (self.queue.get(gate[0]['request_id']) or {}) if gate else {}
            account = self.normalize_account(account, stored.get('client'), stored.get('user_data'),
                                             runtime=stored.get('runtime', 'claude'),
                                             expected=stored.get('account'))  # a leitura das contas pode demorar: fora do lock global
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
                given = account if name == 'account' else args.get(name)
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
        return {**self.queued(request['id']), 'model': row['model'], 'effort': row['effort'],
                'reason': gate['policy_reason']}

    def queued(self, request_id):
        """A resposta do pedido na fila: a posição na ordem da R9.2 e o teto que o segura, se algum."""
        self.prepare()
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
        self.prepare()
        caller = self.caller_of(ctx, args)
        with self.queue.lock:
            self.queue.next_admissible(self.limits)
            pending = self.queue.pending()
            gated = [queue_mod._row(r) for r in self.queue._all(
                "SELECT r.*, g.id AS gate_id, g.reason AS reason FROM requests r LEFT JOIN gates g ON g.request_id = r.id"
                " AND g.approved_at IS NULL WHERE r.state = 'gated' ORDER BY r.id")]
            running = [queue_mod._row(r) for r in self.queue._all("SELECT * FROM requests WHERE state = 'running' ORDER BY id")]
            finished = self.queue.recent_finished(caller) if caller else []

        def brief(r, position=None):
            out = {k: r[k] for k in ('id', 'task', 'role', 'account', 'ref', 'caller')}
            if position is not None:
                out.update({'position': position, 'held_by': r['held_by'], 'held_reason': r['held_reason']})
            if r.get('error'):
                out['error'] = r['error']
            if 'gate_id' in r.keys():
                out['reason'] = r['reason']
                if caller and r['caller'] == caller:  # o id do gate é de quem o abriu: com ele se aprova (R5.2)
                    out['gate_id'] = r['gate_id']
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
