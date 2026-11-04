"""Os tetos do orquestrador (R9.3: o menor manda): workspace, conta e runtime (sessões e projeção de uso) e máquina.

A configuração chega como dicionário com as chaves do `brza.toml` (D4), planas (`'quota.hard_cap_percent'`) ou
aninhadas (`{'quota': {...}}`); o leitor do TOML é do P4. O uso da conta chega por um provedor injetável
`usage(account, runtime) -> {account, runtime, windows: [{name, usedPercent, resetsAt}]} | None` (o leitor do
`orca account list` é do P5), e a máquina por `machine() -> {free_mem_gb, swap_gb, cpu_percent}`.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import accounts as accounts_mod

DEFAULT_HARD_CAP = 90.0
MIN_SLOPE_SPAN_MS = 10 * 60_000  # sem `windowMinutes`, a inclinação só vale com 10 min de amostras
MIN_SLOPE_WINDOW_FRACTION = 20   # com ele, com 1/20 da janela: o `usedPercent` é inteiro e dá degraus
CEILINGS = ('account_active', 'workspace', 'account_sessions', 'account_quota', 'machine')


@dataclass
class Decision:
    admit: bool
    ceiling: str | None  # o teto que segura; None quando admite
    reason: str          # 'ok', 'quota_unknown' ou o motivo de quem segura
    measures: dict = field(default_factory=dict)  # para a linha `route` (P6)


def _get(config, key):
    if key in config:
        return config[key]
    node = config
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---- a máquina ----

def parse_vm_stat(text):
    """Memória livre em GB (livre + inativa + especulativa) da saída do `vm_stat`; None se não reconhece."""
    size = re.search(r'page size of (\d+) bytes', text)
    if not size:
        return None
    pages = 0
    found = False
    for label in ('free', 'inactive', 'speculative'):
        m = re.search(rf'^Pages {label}:\s+(\d+)', text, re.M)
        if m:
            pages += int(m.group(1))
            found = True
    return pages * int(size.group(1)) / 1024 ** 3 if found else None


def parse_swapusage(text):
    """Swap em uso, em GB, da saída do `sysctl vm.swapusage`; None se não reconhece."""
    m = re.search(r'used = ([\d.]+)([MG])', text)
    if not m:
        return None
    value = float(m.group(1))
    return value / 1024 if m.group(2) == 'M' else value


def cpu_percent(loadavg=os.getloadavg, cpus=os.cpu_count):
    """A carga de 1 min sobre o número de CPUs, em %."""
    try:
        return loadavg()[0] / (cpus() or 1) * 100
    except (OSError, AttributeError):
        return None


def _capture(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def read_machine():
    """Os três sinais da máquina; o que não se lê vem None."""
    return {'free_mem_gb': parse_vm_stat(_capture(['vm_stat'])),
            'swap_gb': parse_swapusage(_capture(['sysctl', 'vm.swapusage'])),
            'cpu_percent': cpu_percent()}


# ---- a decisão ----

def _now_ms():
    return int(time.time() * 1000)


class Limits:
    def __init__(self, config=None, usage=None, machine=read_machine, clock=_now_ms, active=None, usage_error=None):
        self.config = config or {}
        self.usage = usage or (lambda account, runtime, client=None, user_data=None: None)
        self.usage_error = usage_error or (lambda runtime, client=None, user_data=None: None)
        self.machine = machine
        self.clock = clock
        self.has_active = active is not None
        self.active = active or (lambda runtime='claude', client=None, user_data=None: None)  # o id da conta ativa do cliente: a do pedido sem `account`
        self.last_decision = None
        self.last_measures = {}
        self._cache = None

    @contextmanager
    def scope(self):
        """Uma passada: o uso de cada (conta, runtime) e a máquina se leem uma vez, por mais pedidos que ela julgue.
        Quem a abre (a `Queue`) segura o lock dela, então as passadas não se cruzam."""
        outer = self._cache
        if outer is None:
            self._cache = {}
        try:
            yield
        finally:
            if outer is None:
                self._cache = None

    def _once(self, key, read):
        if self._cache is None:
            return read()
        if key not in self._cache:
            self._cache[key] = read()
        return self._cache[key]

    def get(self, key):
        return _get(self.config, key)

    def hard_cap(self) -> float:
        """O teto de uso das janelas, como a decisão o lê: `quota.hard_cap_percent`, ou o padrão de 90%."""
        return float(self.get('quota.hard_cap_percent') or DEFAULT_HARD_CAP)

    def decide(self, request, queue) -> Decision:
        """`admit`, ou o teto que segura. A ordem dos tetos é a da R9.3; todo resultado leva as medidas."""
        measures = {}
        decision = (self._account_matches(request, measures) or self._workspace(queue, measures) or self._sessions(request, queue, measures)
                    or self._quota(request, queue, measures) or self._machine(measures))
        # as medidas que a decisão não chegou a tomar ficam de fora; as da máquina entram sempre, só para registrar
        if 'machine' not in measures:
            self._machine(measures)
        if decision is None:
            decision = Decision(True, None, 'quota_unknown' if measures.get('quota', {}).get('known') is False
                                else 'ok')
        decision.measures = measures
        self.last_decision, self.last_measures = decision, measures
        return decision

    def _workspace(self, queue, measures):
        running, cap = queue.running_count(), self.get('orchestrator.max_workers')
        measures['workspace'] = {'running': running, 'max_workers': cap}
        if cap is not None and running >= cap:
            return Decision(False, 'workspace', f'{running} workers abertos, orchestrator.max_workers = {cap}')

    def _call_active(self, runtime, options):
        if runtime in ('grok', 'antigravity'):
            return accounts_mod.SYSTEM_DEFAULT
        try:
            return self.active(runtime, **options)
        except TypeError:
            try:
                return self.active(runtime=runtime, **options)
            except TypeError:
                if runtime != 'claude':
                    return None
                try:
                    return self.active(**options)
                except TypeError:
                    return self.active()

    def _call_usage_error(self, runtime, options):
        try:
            return self.usage_error(runtime, **options)
        except TypeError:
            try:
                return self.usage_error(runtime=runtime, **options)
            except TypeError:
                try:
                    return self.usage_error(**options)
                except TypeError:
                    return self.usage_error()

    def _active(self, request):
        """O id da conta ativa do cliente do pedido (o do `X-Brza-Client`; sem ele, o do processo vivo)."""
        options = self._profile(request)
        runtime = request.get('runtime') or 'claude'
        return self._once(('active', runtime, request.get('client'), request.get('user_data')),
                          lambda: self._call_active(runtime, options))

    @staticmethod
    def _profile(request):
        return {key: request[key] for key in ('client', 'user_data') if request.get(key)}

    def _account_matches(self, request, measures):
        account = request.get('account')
        if not account or not self.has_active:
            return None
        active = self._active(request)
        if account != active:
            measures['account'] = {'account': account, 'active': active}
            return Decision(False, 'account_active',
                            f'a conta pedida {account} não é a ativa ({active or "desconhecida"}); '
                            'o worker-start não tem flag de conta; aguarda a conta pedida ficar ativa')

    def _account(self, request) -> str:
        """A conta do pedido; sem ela (gravada `''`), é a ativa de agora do cliente: o worker abre nela."""
        return request.get('account') or self._active(request) or ''

    def _sessions(self, request, queue, measures):
        account, runtime = self._account(request), request.get('runtime') or ''
        sessions, cap = queue.running_count(account, runtime), self.get('quota.max_sessions_per_account')
        if account and account == self._active(request):
            sessions += queue.running_count('', runtime)  # o worker aberto sem `account` também é da ativa
        measures['account'] = {'account': account, 'runtime': runtime, 'sessions': sessions, 'max_sessions': cap}
        if cap is not None and sessions >= cap:
            return Decision(False, 'account_sessions',
                            f'{sessions} sessões da conta {account or "-"} em {runtime or "-"}, '
                            f'quota.max_sessions_per_account = {cap}')

    def _quota(self, request, queue, measures):
        account, runtime = self._account(request), request.get('runtime') or 'claude'
        cap = self.hard_cap()
        options = self._profile(request)
        snapshot = self._once(('usage', account, runtime, request.get('client'), request.get('user_data')),
                              lambda: self.usage(account, runtime, **options))
        windows = [w for w in (snapshot or {}).get('windows') or [] if w.get('usedPercent') is not None]
        if not windows:
            err = self._once(('usage_error', runtime, request.get('client'), request.get('user_data')),
                             lambda: self._call_usage_error(runtime, options))
            measures['quota'] = {'known': False, 'cap': cap, 'windows': [], 'error': err}
            return None
        report, over = self._report(account, runtime, windows, queue, self.clock()), None
        for w in report:
            if over is None and w['used'] >= cap:
                over = f'janela {w["name"]} já em {w["used"]:g}%, quota.hard_cap_percent = {cap:g}'
            elif over is None and w['projected'] >= cap:
                over = f'janela {w["name"]} projeta {w["projected"]:.0f}% no reset, quota.hard_cap_percent = {cap:g}'
        measures['quota'] = {'known': True, 'cap': cap, 'windows': report}
        if over:
            return Decision(False, 'account_quota', over)

    def _report(self, account, runtime, windows, queue, now):
        """Grava a amostra de cada janela e devolve `used` e `projected` delas: a conta que a decisão e a escolha de
        conta (`pick_account`) fazem é a mesma."""
        self._once(('recorded', account, runtime),
                   lambda: queue.record_usage({'account': account, 'runtime': runtime, 'windows': windows}, now))
        return [{'name': w['name'], 'used': w['usedPercent'], 'resets_at': w.get('resetsAt'),
                 'projected': self._project(queue.samples(account, runtime, w['name'], w.get('resetsAt')), w, now)}
                for w in windows]

    def soft_cap(self):
        value = self.get('quota.soft_cap_percent')
        return float(value) if value is not None else None

    def pick_account(self, snapshots, queue, runtime='claude') -> dict:
        """A conta de menor uso entre as `snapshots` (`{conta: instantâneo}`), pela regra do `choose_account` sobre o
        maior entre o uso e a projeção de cada janela, com o `quota.soft_cap_percent` e o `quota.hard_cap_percent`."""
        from .resolver import choose_account
        now, candidates = self.clock(), {}
        for account, snapshot in snapshots.items():
            windows = [w for w in (snapshot or {}).get('windows') or [] if w.get('usedPercent') is not None]
            if windows:
                report = self._report(account, runtime, windows, queue, now)
                candidates[account] = [max(w['used'], w['projected']) for w in report]
        return choose_account(candidates, self.hard_cap(), soft_cap=self.soft_cap())

    @staticmethod
    def _project(samples, window, now):
        """O `usedPercent` projetado no `resetsAt`, pela inclinação entre a primeira e a última amostra."""
        used, resets = float(window['usedPercent']), window.get('resetsAt')
        if resets is None or len(samples) < 2:
            return used
        (t0, u0), (t1, u1) = samples[0], samples[-1]
        window_ms = (window.get('windowMinutes') or 0) * 60_000
        if t1 - t0 < (window_ms / MIN_SLOPE_WINDOW_FRACTION if window_ms else MIN_SLOPE_SPAN_MS):
            return used  # poucas amostras e muito juntas: o degrau de 1 ponto não é uma inclinação
        slope = (u1 - u0) / (t1 - t0)  # pontos por ms
        return max(used, u1 + slope * max(resets - t1, 0))

    def _machine(self, measures):
        values = self._once('machine', lambda: self.machine() or {})
        rules = (('machine.min_free_mem_gb', 'free_mem_gb', lambda v, c: v < c, 'memória livre'),
                 ('machine.max_swap_gb', 'swap_gb', lambda v, c: v > c, 'swap'),
                 ('machine.max_cpu_percent', 'cpu_percent', lambda v, c: v > c, 'CPU'))
        limits = {key: self.get(key) for key, *_ in rules}
        measures['machine'] = {**{k: values.get(k) for k in ('free_mem_gb', 'swap_gb', 'cpu_percent')},
                               'enforced': any(v is not None for v in limits.values())}
        for key, name, breached, label in rules:
            cap, value = limits[key], values.get(name)
            if cap is not None and value is not None and breached(value, cap):
                return Decision(False, 'machine', f'{label} em {value:.1f}, {key} = {cap}')
