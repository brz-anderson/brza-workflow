"""Os dois clientes do `orca orchestration` (D6): o BrzA Studio (`brza` do app) e o Orca (`orca`).

O cliente vem do header `X-Brza-Client` guardado no pedido (`studio` ou `orca`); sem ele, do processo vivo. Cada um traz
o binário do CLI e o `ORCA_USER_DATA_PATH` que o H4 exige no `env` do subprocesso.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SUPPORT = Path.home() / 'Library' / 'Application Support'
CLIENTS = {
    'studio': {'cli': '/Applications/BrzA Studio.app/Contents/Resources/bin/brza',
               'user_data': str(SUPPORT / 'brza-studio'), 'process': 'BrzA Studio.app/Contents/MacOS/BrzA Studio'},
    'orca': {'cli': 'orca', 'user_data': str(SUPPORT / 'orca'), 'process': 'Orca.app/Contents/MacOS/Orca'},
}


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
