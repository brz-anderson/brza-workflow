"""Os tetos do orquestrador (R9.3: o menor manda): workspace, conta e runtime (sessões e projeção de uso) e máquina.

A configuração chega como dicionário com as chaves do `brza.toml` (D4), planas (`'quota.hard_cap_percent'`) ou
aninhadas (`{'quota': {...}}`); o leitor do TOML é do P4. O uso da conta chega por um provedor injetável
`usage(account, runtime) -> {account, runtime, windows: [{name, usedPercent, resetsAt}]} | None` (o leitor do
`orca account list` é do P5), e a máquina por `machine() -> {free_mem_gb, swap_used_gb, cpu_percent}`.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field

DEFAULT_HARD_CAP = 90.0
CEILINGS = ('workspace', 'account_sessions', 'account_quota', 'machine')


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
            'swap_used_gb': parse_swapusage(_capture(['sysctl', 'vm.swapusage'])),
            'cpu_percent': cpu_percent()}


# ---- a decisão ----

def _now_ms():
    return int(time.time() * 1000)


class Limits:
    def __init__(self, config=None, usage=None, machine=read_machine, clock=_now_ms):
        self.config = config or {}
        self.usage = usage or (lambda account, runtime: None)
        self.machine = machine
        self.clock = clock
        self.last_decision = None
        self.last_measures = {}

    def get(self, key):
        return _get(self.config, key)

    def decide(self, request, queue) -> Decision:
        """`admit`, ou o teto que segura. A ordem dos tetos é a da R9.3; todo resultado leva as medidas."""
        measures = {}
        decision = (self._workspace(queue, measures) or self._sessions(request, queue, measures)
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

    def _sessions(self, request, queue, measures):
        account, runtime = request.get('account') or '', request.get('runtime') or ''
        sessions, cap = queue.running_count(account, runtime), self.get('quota.max_sessions_per_account')
        measures['account'] = {'account': account, 'runtime': runtime, 'sessions': sessions, 'max_sessions': cap}
        if cap is not None and sessions >= cap:
            return Decision(False, 'account_sessions',
                            f'{sessions} sessões da conta {account or "-"} em {runtime or "-"}, '
                            f'quota.max_sessions_per_account = {cap}')

    def _quota(self, request, queue, measures):
        account, runtime = request.get('account') or '', request.get('runtime') or ''
        cap = float(self.get('quota.hard_cap_percent') or DEFAULT_HARD_CAP)
        snapshot = self.usage(account, runtime)
        windows = [w for w in (snapshot or {}).get('windows') or [] if w.get('usedPercent') is not None]
        if not windows:
            measures['quota'] = {'known': False, 'cap': cap, 'windows': []}  # R2.5: só o teto de sessões vale
            return None
        now = self.clock()
        queue.record_usage({'account': account, 'runtime': runtime, 'windows': windows}, now)
        report, over = [], None
        for w in windows:
            projected = self._project(queue.samples(account, runtime, w['name'], w.get('resetsAt')), w, now)
            report.append({'name': w['name'], 'used': w['usedPercent'], 'resets_at': w.get('resetsAt'),
                           'projected': projected})
            if over is None and w['usedPercent'] >= cap:
                over = f'janela {w["name"]} já em {w["usedPercent"]:g}%, quota.hard_cap_percent = {cap:g}'
            elif over is None and projected >= cap:
                over = f'janela {w["name"]} projeta {projected:.0f}% no reset, quota.hard_cap_percent = {cap:g}'
        measures['quota'] = {'known': True, 'cap': cap, 'windows': report}
        if over:
            return Decision(False, 'account_quota', over)

    @staticmethod
    def _project(samples, window, now):
        """O `usedPercent` projetado no `resetsAt`, pela inclinação entre a primeira e a última amostra."""
        used, resets = float(window['usedPercent']), window.get('resetsAt')
        if resets is None or len(samples) < 2:
            return used
        (t0, u0), (t1, u1) = samples[0], samples[-1]
        if t1 <= t0:
            return used
        slope = (u1 - u0) / (t1 - t0)  # pontos por ms
        return max(used, u1 + slope * max(resets - t1, 0))

    def _machine(self, measures):
        values = self.machine() or {}
        rules = (('machine.min_free_mem_gb', 'free_mem_gb', lambda v, c: v < c, 'memória livre'),
                 ('machine.max_swap_gb', 'swap_used_gb', lambda v, c: v > c, 'swap'),
                 ('machine.max_cpu_percent', 'cpu_percent', lambda v, c: v > c, 'CPU'))
        limits = {key: self.get(key) for key, *_ in rules}
        measures['machine'] = {**{k: values.get(k) for k in ('free_mem_gb', 'swap_used_gb', 'cpu_percent')},
                               'enforced': any(v is not None for v in limits.values())}
        for key, name, breached, label in rules:
            cap, value = limits[key], values.get(name)
            if cap is not None and value is not None and breached(value, cap):
                return Decision(False, 'machine', f'{label} em {value:.1f}, {key} = {cap}')
