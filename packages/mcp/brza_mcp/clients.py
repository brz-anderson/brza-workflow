"""Os dois clientes do `orca orchestration` (D6): o BrzA Studio (`brza` do app) e o Orca (`orca`).

O cliente vem do header `X-Brza-Client` guardado no pedido (`studio` ou `orca`); sem ele, do processo vivo. Cada um traz
o binário do CLI e o `ORCA_USER_DATA_PATH` que o H4 exige no `env` do subprocesso.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SUPPORT = Path.home() / 'Library' / 'Application Support'
CLIENTS = {
    'studio': {'cli': '/Applications/BrzA Studio.app/Contents/Resources/bin/brza',
               'user_data': str(SUPPORT / 'brza-studio'), 'process': 'BrzA Studio.app/Contents/MacOS/BrzA Studio'},
    'orca': {'cli': '/Applications/Orca.app/Contents/Resources/bin/orca', 'path_name': 'orca', 'user_data': str(SUPPORT / 'orca'), 'process': 'Orca.app/Contents/MacOS/Orca'},
}


def cli_path(name) -> str:
    """O binário do CLI do cliente: o do app, e o do PATH como reserva. O agente do launchd não tem `/Applications/...`
    no PATH, então o nome solto (`orca`) não se acha lá."""
    spec = CLIENTS[name]
    if Path(spec['cli']).exists() or not spec.get('path_name'):
        return spec['cli']  # o `brza` do Studio não tem reserva: no PATH `brza` é o CLI do workflow
    return shutil.which(spec['path_name']) or spec['cli']


def from_header(value) -> str | None:
    """O cliente do `X-Brza-Client`: o nome (`studio`, `orca`) ou o `${ORCA_USER_DATA_PATH}` do cliente, de onde sai o
    nome pela pasta (`brza-studio`, `orca`). Outro valor não é cliente."""
    if value in CLIENTS:
        return value
    if isinstance(value, str) and value.startswith('/'):
        folder = Path(value.rstrip('/')).name
        return next((n for n, spec in CLIENTS.items() if Path(spec['user_data']).name == folder), None)
    return None


def resolve_header(value):
    """`(cliente, user_data)` do `X-Brza-Client`. O nome ou o caminho de uma pasta conhecida dá o cliente e nenhum
    `user_data` (vale o padrão dele). Outra pasta com o `orca-runtime.json` dentro é o perfil de um build (o
    `brza-dev`, por exemplo): o runtime dela é outro, e o binário do Studio serve a ela com o
    `ORCA_USER_DATA_PATH` dela. O resto não é cliente."""
    client = from_header(value)
    if client or not (isinstance(value, str) and value.startswith('/')):
        return client, None
    path = Path(value.rstrip('/'))
    return ('studio', str(path)) if (path / 'orca-runtime.json').is_file() else (None, None)


def detect(run=None) -> str | None:
    """O cliente do processo vivo (`pgrep -f`); o Studio vence quando os dois rodam. None se nenhum."""
    run = run or _pgrep
    for name, spec in CLIENTS.items():
        if run(spec['process']):
            return name
    return None


def _pgrep(pattern) -> bool:
    try:
        return subprocess.run(['pgrep', '-f', pattern], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
