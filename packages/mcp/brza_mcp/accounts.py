"""As contas do cliente (D6): o `account list --json` vira contas e instantâneos de uso, o `claude_env` monta o env do
`claude -p` pela regra do H2, e `switch_chat_account` troca a conta de uma sessão de chat pelo RPC do H3.

O uso entra no formato que o `Limits` consome, `{account, runtime, windows: [{name, usedPercent, resetsAt}]}`: a conta
ativa vem de `rateLimits.claude` e as outras de `rateLimits.inactiveClaudeAccounts`. Conta sem uso fica sem instantâneo
(o `Limits` aplica a R2.5). Cada janela é a chave do bloco (`session`, `weekly`, ...), com `windowMinutes`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import clients

SYSTEM_DEFAULT = 'systemDefault'
RUNTIME = 'claude'
_WINDOW_KEYS = ('session', 'weekly', 'fableWeekly', 'monthly')


class AccountError(RuntimeError):
    pass


def _runner(argv, env=None, timeout=30):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise AccountError(f'{argv[0]}: {e}') from None
    return r.returncode, r.stdout, r.stderr


def _windows(block):
    out = []
    for key in _WINDOW_KEYS:
        w = (block or {}).get(key)
        if isinstance(w, dict) and w.get('usedPercent') is not None:
            out.append({'name': key, 'usedPercent': w['usedPercent'], 'resetsAt': w.get('resetsAt'),
                        'windowMinutes': w.get('windowMinutes')})
    return out


def parse(payload) -> dict:
    """O JSON do `account list` (com ou sem o envelope `{ok, result}`) -> `{accounts, usage}`.

    `accounts`: `[{id, label, email, home, system_default, active}]`, a `systemDefault` primeiro; `usage`:
    `{id: {account, runtime, windows}}`, só das contas com uso."""
    data = payload.get('result', payload) if isinstance(payload, dict) else {}
    claude = data.get('claude')
    managed = claude.get('accounts') if isinstance(claude, dict) else None
    if managed is None:  # a forma da fixture: as contas em `accounts.claude`
        managed = (data.get('accounts') or {}).get('claude') if isinstance(data.get('accounts'), dict) else None
    claude = claude if isinstance(claude, dict) else {}
    active = claude.get('activeAccountId')
    default_home = claude.get('systemDefaultHome')
    accounts = [{'id': SYSTEM_DEFAULT, 'label': claude.get('systemDefaultEmail') or SYSTEM_DEFAULT,
                 'email': claude.get('systemDefaultEmail'), 'home': default_home, 'system_default': True,
                 'active': active is None}]
    for a in managed or []:
        accounts.append({'id': a['id'], 'label': a.get('email') or a['id'], 'email': a.get('email'),
                         'home': a.get('home'), 'system_default': False, 'active': a['id'] == active})
    rate = data.get('rateLimits') or {}
    usage = {}
    windows = _windows(rate.get('claude'))
    if windows:
        usage[active or SYSTEM_DEFAULT] = {'account': active or SYSTEM_DEFAULT, 'runtime': RUNTIME, 'windows': windows}
    for item in rate.get('inactiveClaudeAccounts') or []:
        windows = _windows(item.get('rateLimits'))
        if windows and item.get('accountId'):
            usage[item['accountId']] = {'account': item['accountId'], 'runtime': RUNTIME, 'windows': windows}
    return {'accounts': accounts, 'usage': usage}


class Accounts:
    """O leitor das contas, com o `account list --json` do cliente e um cache curto (o `Limits` o chama por pedido)."""

    def __init__(self, client=None, run=_runner, detect=clients.detect, ttl=30.0, clock=time.monotonic):
        self.client, self.run, self.detect, self.ttl, self.clock = client, run, detect, ttl, clock
        self._cache, self._at = None, None
        self._lock = threading.Lock()

    def read(self, force=False) -> dict:
        with self._lock:
            if not force and self._cache is not None and self.clock() - self._at < self.ttl:
                return self._cache
            name = self.client or self.detect()
            if name not in clients.CLIENTS:
                raise AccountError('nenhum cliente (BrzA Studio ou Orca) de pé')
            spec = clients.CLIENTS[name]
            env = {k: v for k, v in os.environ.items() if not k.startswith('ORCA_')}
            env['ORCA_USER_DATA_PATH'] = spec['user_data']
            code, out, err = self.run([spec['cli'], 'account', 'list', '--json'], env, 30)
            try:
                if code != 0:
                    raise ValueError(err.strip() or out.strip())
                self._cache, self._at = parse(json.loads(out)), self.clock()
            except ValueError as e:
                raise AccountError(f'account list falhou: {e}') from None
            return self._cache

    def accounts(self):
        return self.read()['accounts']

    def find(self, name, data=None):
        """A conta pelo id, e-mail ou rótulo; sem nome, a ativa. None se não existe."""
        accounts = (data or self.read())['accounts']
        if not name:
            return next((a for a in accounts if a['active']), None)
        return next((a for a in accounts if name in (a['id'], a['email'], a['label'])), None)

    def usage(self, account, runtime):
        """O provedor do `Limits`: o instantâneo da conta, ou None (sem uso, sem conta ou sem cliente)."""
        if runtime not in ('', None, RUNTIME):
            return None
        try:
            data = self.read()
        except AccountError:
            return None
        found = self.find(account, data)
        return data['usage'].get(found['id']) if found else None


def claude_env(account, base=None) -> dict:
    """O env do `claude -p` da conta (H2): a gerenciada leva o `CLAUDE_CONFIG_DIR` do `home`; a `systemDefault` vai SEM
    a variável (com ela o Keychain falha com "OAuth session expired")."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k != 'CLAUDE_CONFIG_DIR'}
    if account and not account.get('system_default') and account.get('home'):
        env['CLAUDE_CONFIG_DIR'] = account['home']
    return env


# ---- a troca de conta numa sessão de chat (H3) ----

_ENV_LOCK = threading.Lock()


@contextmanager
def _client_env(user_data):
    """O `brza_runtime.rpc` lê o `ORCA_USER_DATA_PATH` do ambiente do processo: troca-o só durante a chamada."""
    with _ENV_LOCK:
        old = os.environ.get('ORCA_USER_DATA_PATH')
        os.environ['ORCA_USER_DATA_PATH'] = user_data
        try:
            yield
        finally:
            if old is None:
                os.environ.pop('ORCA_USER_DATA_PATH', None)
            else:
                os.environ['ORCA_USER_DATA_PATH'] = old


def _runtime_module():
    bin_dir = str(Path(__file__).resolve().parents[3] / 'bin')
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    import brza_runtime
    return brza_runtime


def switch_chat_account(session_id, account_id, client='studio', rpc=None):
    """Troca a conta do chat `session_id` para `account_id` (None = `systemDefault`) pelo RPC
    `agentSession.switchAccount`. Só a sessão em repouso aceita: em turno ativo levanta `AccountError` e não tenta de
    novo. Devolve o resultado do RPC. Chame só com o gate de troca de conta aprovado (R5.2)."""
    runtime = _runtime_module() if rpc is None else None
    call = rpc or runtime.rpc
    account_id = None if account_id in (None, SYSTEM_DEFAULT) else account_id
    fields = {'accountId': account_id}
    try:
        with _client_env(clients.CLIENTS[client]['user_data']):
            page = call('agentSession.history', {'sessionId': session_id, 'direction': 'tail', 'limit': 1})['page']
            import hashlib
            payload = _canonical({'method': 'agentSession.switchAccount', 'sessionId': session_id, 'fields': fields})
            result = call('agentSession.switchAccount', {
                'envelope': {'sessionId': session_id, 'clientOperationId': f'{int(time.time() * 1000)}-{uuid.uuid4().hex}',
                             'expectedRuntimeFence': page['fence'],
                             'payloadFingerprint': hashlib.sha256(payload.encode()).hexdigest()},
                **fields})
    except Exception as e:  # BrzaError do runtime, ou o que o `rpc` injetado levantar
        message = str(e)
        if 'turnActive' in message or 'agent_session_operation_invalid' in message:
            raise AccountError(f'a sessão {session_id} está em turno ativo: a troca de conta só vale em repouso; '
                               'espere o turno acabar e peça de novo') from None
        raise AccountError(f'a troca de conta da sessão {session_id} falhou: {message}') from None
    if isinstance(result, dict) and result.get('kind') == 'not-done':
        raise AccountError(f'o app recusou a troca de conta: {result.get("notice")}')
    return result


def _canonical(value) -> str:
    if isinstance(value, list):
        return '[' + ','.join(_canonical(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(f'{json.dumps(k, ensure_ascii=False)}:{_canonical(v)}'
                              for k, v in sorted(value.items())) + '}'
    return json.dumps(value, ensure_ascii=False)


# ---- o relatório do `brza quota` ----

def quota_report(data, queue, limits, now_ms=None) -> list:
    """Por conta, as janelas com `used`, `resets_at` e a projeção (a do `Limits._project`). Grava as amostras na fila,
    como o `Limits` faz a cada decisão, para a inclinação ter história."""
    from .limits import Limits
    now = int(time.time() * 1000) if now_ms is None else now_ms
    cap = float(limits.get('quota.hard_cap_percent') or 90.0)
    report = []
    for a in data['accounts']:
        snap = data['usage'].get(a['id'])
        windows = []
        if snap:
            queue.record_usage({'account': a['id'], 'runtime': RUNTIME, 'windows': snap['windows']}, now)
            for w in snap['windows']:
                samples = queue.samples(a['id'], RUNTIME, w['name'], w.get('resetsAt'))
                windows.append({'name': w['name'], 'used': w['usedPercent'], 'resets_at': w.get('resetsAt'),
                                'projected': Limits._project(samples, w, now)})
        report.append({'account': a['id'], 'label': a['label'], 'system_default': a['system_default'],
                       'active': a['active'], 'cap': cap, 'windows': windows})
    return report
