"""A telemetria `route` (D5, PLN-003/M16): uma linha `event: "route"` por decisão de roteamento no
`<BRZA_WORKSPACE>/.measurements/measurements.jsonl`, o mesmo log do hook de medição, que o `summarize_measurements.py` lê.

    telemetry = Telemetry(workspace)
    telemetry.emit('queued', request_row, reason='policy')

A forma é a do hook (`core/.claude/hooks/measure.py`): `timestamp` ISO com fuso, e não o `ts` do exemplo do D5, para que
o filtro `--since` do resumo valha também para estas linhas. O `workstream` é o da branch do `BRZA_WORKSPACE`
(`workstream/<x>` -> `x`; a branch da norma fica com o nome dela), porque o servidor não vê o cwd de quem chama.

A linha sai numa só `write` com `O_APPEND` (atômica para um processo só e entre processos num sistema local) e sob um lock
entre threads. Nada aqui levanta: sem workspace, sem permissão ou com o disco cheio, a linha se perde e o motivo vai uma vez
ao log de erro do runtime; o pedido segue.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone

LOG_DIR = '.measurements'
LOG_FILE = 'measurements.jsonl'
WORKSTREAM_PREFIX = 'workstream/'
_UNSET = object()
_MACHINE_KEYS = (('free_mem_gb', 'free_mem_gb'), ('swap_gb', 'swap_used_gb'), ('cpu_percent', 'cpu_percent'))


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _stderr(message):
    print(f'telemetria route: {message}', file=sys.stderr, flush=True)


def machine_of(measures):
    """O bloco `machine` da linha, das medidas do `Limits` (`swap_used_gb` vira `swap_gb`, como no D5)."""
    machine = (measures or {}).get('machine')
    if not machine:
        return None
    out = {name: machine.get(key) for name, key in _MACHINE_KEYS}
    out['enforced'] = bool(machine.get('enforced'))
    return out


class Telemetry:
    def __init__(self, workspace=None, workstream=_UNSET, log=None, clock=_now):
        self.workspace = workspace or None
        self.path = os.path.join(self.workspace, LOG_DIR, LOG_FILE) if self.workspace else None
        self._workstream = workstream
        self.log = log or _stderr
        self.clock = clock
        self.lock = threading.Lock()
        self.warned = False
        self.states = {}  # pedido -> a última decisão de admissão escrita, para não repetir a cada `status`

    # ---- o workstream ----

    def workstream(self):
        if self._workstream is _UNSET:
            self._workstream = None
            try:
                out = subprocess.run(['git', '-C', self.workspace, 'rev-parse', '--abbrev-ref', 'HEAD'],
                                     capture_output=True, text=True, timeout=5)
                branch = out.stdout.strip() if out.returncode == 0 else ''
            except (OSError, subprocess.SubprocessError):
                branch = ''
            if branch and branch != 'HEAD':
                self._workstream = branch[len(WORKSTREAM_PREFIX):] if branch.startswith(WORKSTREAM_PREFIX) else branch
        return self._workstream

    # ---- a linha ----

    def emit(self, decision, request=None, **fields) -> bool:
        """Grava a linha e devolve se gravou. `request` é a linha do pedido na fila (ou só `{'id': ...}`); `fields`
        sobrepõe qualquer campo (`model`, `effort`, `reason`, `held_by`, `resolver`, `measures`, `gate`...)."""
        if not self.path:
            return False
        try:
            request = request or {}
            line = {'timestamp': self.clock(), 'event': 'route', 'workstream': self.workstream(),
                    'caller': request.get('caller'), 'request': request.get('id'), 'decision': decision,
                    'model': request.get('model'), 'effort': request.get('effort'), 'account': request.get('account'),
                    'reason': None, 'held_by': None, 'machine': None, 'resolver': None}
            line.update(fields)
            if line['machine'] is None and line.get('measures'):
                line['machine'] = machine_of(line['measures'])
            data = (json.dumps(line, ensure_ascii=False) + '\n').encode()
            with self.lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            return True
        except Exception as e:  # nenhuma falha daqui derruba o pedido
            if not self.warned:
                self.warned = True
                try:
                    self.log(f'não gravei em {self.path}: {e}')
                except Exception:
                    pass
            return False

    # ---- os pontos de decisão ----

    def decided(self, request, decision, chosen):
        """O gancho da `Queue.next_admissible`: a retenção (o teto, o motivo e as medidas) e a admissão do primeiro
        admissível. Cada mudança vira uma linha; a repetição, a cada `status` ou volta do ciclo, não."""
        try:
            if not decision.admit:
                state = ('held', decision.ceiling)
            elif chosen:
                state = ('admitted', None)
            else:
                return
            if self.states.get(request['id']) == state:
                return
            self.states[request['id']] = state
            self.emit(state[0], request, reason=decision.reason, held_by=decision.ceiling,
                      measures=decision.measures)
        except Exception:
            pass

    def policy(self, request, decided):
        """A decisão da política (`decide_model`) e, quando houve, o desempate do `resolver` que veio com ela."""
        resolved = decided.get('resolver')
        if resolved:
            self.emit('resolved', request, reason=resolved.get('reason'), resolver=resolved)
        self.emit('policy', request, model=decided.get('model'), effort=decided.get('effort'),
                  reason=decided.get('reason'), passthrough=decided.get('reason') == 'passthrough')
