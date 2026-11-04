"""As contas do cliente (D6): o `account list --json` vira contas e instantâneos de uso, o `claude_env` monta o env do
`claude -p` pela regra do H2, e `switch_chat_account` troca a conta de uma sessão de chat pelo RPC do H3.

O uso entra no formato que o `Limits` consome, `{account, runtime, windows: [{name, usedPercent, resetsAt}]}`: a conta
ativa vem de `rateLimits.claude` e as outras de `rateLimits.inactiveClaudeAccounts`. Conta sem uso fica sem instantâneo
(o `Limits` aplica a R2.5). Cada janela é a chave do bloco (`session`, `weekly`, ...), com `windowMinutes`.
"""
from __future__ import annotations

import hashlib
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
    """O JSON do `account list` (com ou sem o envelope `{ok, result}`) ->
    `{runtime: {'accounts': [...], 'usage': {...}, 'error': ...}}`.

    `accounts`: `[{id, label, email, home, system_default, active}]`, a `systemDefault` primeiro; `usage`:
    `{id: {account, runtime, windows}}`, só das contas com uso."""
    data = payload.get('result', payload) if isinstance(payload, dict) else {}
    rate = data.get('rateLimits') or {}

    # claude: como hoje (claude, rateLimits.claude, rateLimits.inactiveClaudeAccounts)
    claude = data.get('claude')
    managed = claude.get('accounts') if isinstance(claude, dict) else None
    claude = claude if isinstance(claude, dict) else {}
    active = claude.get('activeAccountId')
    default_home = claude.get('systemDefaultHome')
    claude_accounts = [{'id': SYSTEM_DEFAULT, 'label': claude.get('systemDefaultEmail') or SYSTEM_DEFAULT,
                        'email': claude.get('systemDefaultEmail'), 'home': default_home, 'system_default': True,
                        'active': active is None}]
    for a in managed or []:
        claude_accounts.append({'id': a['id'], 'label': a.get('email') or a['id'], 'email': a.get('email'),
                                'home': a.get('home'), 'system_default': False, 'active': a['id'] == active})
    rate_claude = rate.get('claude') or {}
    claude_usage = {}
    windows = _windows(rate_claude)
    if windows:
        claude_usage[active or SYSTEM_DEFAULT] = {'account': active or SYSTEM_DEFAULT, 'runtime': 'claude', 'windows': windows}
    for item in rate.get('inactiveClaudeAccounts') or []:
        windows = _windows(item.get('rateLimits'))
        if windows and item.get('accountId'):
            claude_usage[item['accountId']] = {'account': item['accountId'], 'runtime': 'claude', 'windows': windows}
    claude_error = rate_claude.get('error')

    # codex: codex.systemDefault.email e codex.accounts (sem home); ativa = codex.activeAccountId;
    #        o uso da ativa em rateLimits.codex, o das outras em rateLimits.inactiveCodexAccounts
    codex_data = data.get('codex') if isinstance(data.get('codex'), dict) else {}
    codex_active = codex_data.get('activeAccountId')
    sys_default = codex_data.get('systemDefault')
    sys_email = sys_default.get('email') if isinstance(sys_default, dict) else (codex_data.get('systemDefaultEmail') or None)
    codex_accounts = [{'id': SYSTEM_DEFAULT, 'label': sys_email or SYSTEM_DEFAULT,
                       'email': sys_email, 'home': None, 'system_default': True,
                       'active': codex_active is None}]
    for a in codex_data.get('accounts') or []:
        codex_accounts.append({'id': a['id'], 'label': a.get('email') or a['id'], 'email': a.get('email'),
                               'home': None, 'system_default': False, 'active': a['id'] == codex_active})
    if codex_active and not any(a['id'] == codex_active for a in codex_accounts):
        codex_accounts.append({'id': codex_active, 'label': codex_active, 'email': None,
                               'home': None, 'system_default': False, 'active': True})
    rate_codex = rate.get('codex') or {}
    codex_usage = {}
    windows = _windows(rate_codex)
    if windows:
        codex_usage[codex_active or SYSTEM_DEFAULT] = {'account': codex_active or SYSTEM_DEFAULT, 'runtime': 'codex', 'windows': windows}
    for item in rate.get('inactiveCodexAccounts') or []:
        windows = _windows(item.get('rateLimits'))
        if windows and item.get('accountId'):
            codex_usage[item['accountId']] = {'account': item['accountId'], 'runtime': 'codex', 'windows': windows}
    codex_error = rate_codex.get('error')

    # grok, antigravity: uma conta, {'id': 'systemDefault', 'label': <runtime>, 'system_default': True, 'active': True},
    #        com o uso das janelas de topo de rateLimits.<runtime> (R3.2); os buckets do Antigravity ficam de fora
    grok_accounts = [{'id': SYSTEM_DEFAULT, 'label': 'grok', 'email': None, 'home': None,
                      'system_default': True, 'active': True}]
    rate_grok = rate.get('grok') or {}
    grok_usage = {}
    windows = _windows(rate_grok)
    if windows:
        grok_usage[SYSTEM_DEFAULT] = {'account': SYSTEM_DEFAULT, 'runtime': 'grok', 'windows': windows}
    grok_error = rate_grok.get('error')

    antigravity_accounts = [{'id': SYSTEM_DEFAULT, 'label': 'antigravity', 'email': None, 'home': None,
                             'system_default': True, 'active': True}]
    rate_antigravity = rate.get('antigravity') or {}
    antigravity_usage = {}
    windows = _windows(rate_antigravity)
    if windows:
        antigravity_usage[SYSTEM_DEFAULT] = {'account': SYSTEM_DEFAULT, 'runtime': 'antigravity', 'windows': windows}
    antigravity_error = rate_antigravity.get('error')

    return {
        'claude': {'accounts': claude_accounts, 'usage': claude_usage, 'error': claude_error},
        'codex': {'accounts': codex_accounts, 'usage': codex_usage, 'error': codex_error},
        'grok': {'accounts': grok_accounts, 'usage': grok_usage, 'error': grok_error},
        'antigravity': {'accounts': antigravity_accounts, 'usage': antigravity_usage, 'error': antigravity_error},
    }


class Accounts:
    """O leitor das contas, com o `account list --json` do cliente e um cache curto (o `Limits` o chama por pedido).

    O cliente é o do pedido (`client`): com o Studio e o Orca de pé, a conta e o uso de um pedido do Orca vêm do Orca. O
    cache é por cliente e perfil, e a leitura que falha também se guarda pelo mesmo TTL, para o app travado não custar 30 s a
    cada consulta."""

    def __init__(self, client=None, run=_runner, detect=clients.detect, ttl=30.0, clock=time.monotonic):
        self.client, self.run, self.detect, self.ttl, self.clock = client, run, detect, ttl, clock
        self._cache, self._failed = {}, {}
        self._lock = threading.Lock()

    def read(self, force=False, client=None, user_data=None) -> dict:
        key = (client or self.client, user_data)  # o cache separa também os perfis de outro build
        with self._lock:
            now = self.clock()
            hit = self._cache.get(key)
            if not force and hit and now - hit[1] < self.ttl:
                return hit[0]
            miss = self._failed.get(key)
            if not force and miss and now - miss[1] < self.ttl:
                raise AccountError(miss[0])
            try:
                data = self._read(*key)
            except AccountError as e:
                self._failed[key] = (str(e), self.clock())
                raise
            self._cache[key], _ = (data, self.clock()), self._failed.pop(key, None)
            return data

    def _read(self, key, user_data=None) -> dict:
        name = key or self.detect()
        if name not in clients.CLIENTS:
            raise AccountError('nenhum cliente (BrzA Studio ou Orca) de pé')
        spec = clients.CLIENTS[name]
        env = {k: v for k, v in os.environ.items() if not k.startswith('ORCA_')}
        env['ORCA_USER_DATA_PATH'] = user_data or spec['user_data']
        code, out, err = self.run([clients.cli_path(name), 'account', 'list', '--json'], env, 30)
        try:
            if code != 0:
                raise ValueError(err.strip() or out.strip())
            return parse(json.loads(out))
        except ValueError as e:
            raise AccountError(f'account list falhou: {e}') from None

    def warm(self, client=None, user_data=None):
        """Aquece o cache fora de qualquer lock; a falha fica guardada e não levanta."""
        try:
            self.read(client=client, user_data=user_data)
        except AccountError:
            pass

    def accounts(self, runtime='claude', client=None, user_data=None):
        runtime = runtime or 'claude'
        return self.read(client=client, user_data=user_data).get(runtime, {}).get('accounts', [])

    def find(self, name, runtime='claude', data=None, client=None, user_data=None):
        """A conta pelo id, e-mail ou rótulo; sem nome, a ativa. None se não existe."""
        if isinstance(runtime, dict) and data is None:
            data, runtime = runtime, 'claude'
        runtime = runtime or 'claude'
        read_data = data or self.read(client=client, user_data=user_data)
        runtime_data = read_data.get(runtime, {})
        accounts = runtime_data.get('accounts', [])
        if not name:
            return next((a for a in accounts if a['active']), None)
        return next((a for a in accounts if name in (a['id'], a['email'], a['label'])), None)

    def active_id(self, runtime='claude', client=None, user_data=None):
        """O id da conta ativa do cliente para o runtime, ou None se não se lê."""
        runtime = runtime or 'claude'
        try:
            found = self.find(None, runtime=runtime, client=client, user_data=user_data)
        except AccountError:
            return None
        return found['id'] if found else None

    def usage(self, account, runtime='claude', client=None, user_data=None):
        """O provedor do `Limits`: o instantâneo da conta, ou None (sem uso, sem conta ou sem cliente)."""
        runtime = runtime or 'claude'
        try:
            data = self.read(client=client, user_data=user_data)
        except AccountError:
            return None
        runtime_data = data.get(runtime, {})
        found = self.find(account, runtime=runtime, data=data)
        return runtime_data.get('usage', {}).get(found['id']) if found else None

    def usage_error(self, runtime='claude', client=None, user_data=None):
        """O erro do bloco de rateLimits do runtime, ou None."""
        runtime = runtime or 'claude'
        try:
            data = self.read(client=client, user_data=user_data)
        except AccountError:
            return None
        return data.get(runtime, {}).get('error')


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
            payload = _runtime_module().canonicalize(
                {'method': 'agentSession.switchAccount', 'sessionId': session_id, 'fields': fields})
            result = call('agentSession.switchAccount', {
                'envelope': {'sessionId': session_id, 'clientOperationId': f'{int(time.time() * 1000)}-{uuid.uuid4().hex}',
                             'expectedRuntimeFence': page['fence'],
                             'payloadFingerprint': hashlib.sha256(payload.encode()).hexdigest()},
                **fields})
    except Exception as e:  # BrzaError do runtime, ou o que o `rpc` injetado levantar
        message = str(e)
        if 'turnActive' in message:
            raise AccountError(f'a sessão {session_id} está em turno ativo: a troca de conta só vale em repouso; '
                               'espere o turno acabar e peça de novo') from None
        raise AccountError(f'a troca de conta da sessão {session_id} falhou: {message}') from None
    if isinstance(result, dict) and result.get('kind') == 'not-done':
        raise AccountError(f'o app recusou a troca de conta: {result.get("notice")}')
    return result


# ---- o relatório do `brza quota` ----

def quota_report(data, queue, limits, now_ms=None) -> list:
    """Por conta e runtime, as janelas com `used`, `resets_at` e a projeção (a do `Limits._project`). Grava as amostras
    na fila, como o `Limits` faz a cada decisão, para a inclinação ter história."""
    from .limits import Limits
    now = int(time.time() * 1000) if now_ms is None else now_ms
    cap = limits.hard_cap()
    report = []
    runtimes_to_process = [r for r in ('claude', 'codex', 'grok', 'antigravity') if r in data]
    if not runtimes_to_process and 'accounts' in data:
        runtimes_to_process = ['claude']
        data = {'claude': data}
    for rt in runtimes_to_process:
        rt_data = data[rt]
        for a in rt_data.get('accounts', []):
            snap = rt_data.get('usage', {}).get(a['id'])
            windows = []
            if snap:
                queue.record_usage({'account': a['id'], 'runtime': rt, 'windows': snap['windows']}, now)
                for w in snap['windows']:
                    samples = queue.samples(a['id'], rt, w['name'], w.get('resetsAt'))
                    windows.append({'name': w['name'], 'used': w['usedPercent'], 'resets_at': w.get('resetsAt'),
                                    'projected': Limits._project(samples, w, now)})
            report.append({'runtime': rt, 'account': a['id'], 'label': a['label'],
                           'system_default': a['system_default'], 'active': a['active'],
                           'cap': cap, 'windows': windows})
    return report
