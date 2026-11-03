"""O agente do launchd: o plist, o launchctl e a espera do /health."""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import LABEL, paths

ENTRY = Path(__file__).resolve().parents[1] / 'bin' / 'brza-mcp'


def run_launchctl(*args) -> subprocess.CompletedProcess:
    """Todo chamado ao launchctl passa por aqui; os testes trocam esta função."""
    return subprocess.run(['launchctl', *args], capture_output=True, text=True)


def domain() -> str:
    return f'gui/{os.getuid()}'


def target() -> str:
    return f'{domain()}/{LABEL}'


def agent_path() -> str:
    """O PATH do agente: o launchd sobe com o mínimo, e o claude mora em ~/.local/bin."""
    return ':'.join([str(Path.home() / '.local' / 'bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin',
                     '/usr/sbin', '/sbin'])


def build_plist() -> dict:
    env = {'HOME': str(Path.home()), 'PATH': agent_path()}
    root = paths.workspace_root()  # o serve acha o brza.toml do workspace fora do cwd do terminal
    if root is not None:
        env['BRZA_WORKSPACE'] = str(root)
    for var in ('BRZA_HOME', 'BRZA_MCP_PORT'):  # o agente vê a mesma base e a mesma porta que quem o carregou
        if os.environ.get(var):
            env[var] = os.environ[var]
    return {'Label': LABEL,
            'ProgramArguments': [sys.executable, str(ENTRY), 'serve'],
            'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False},
            'StandardOutPath': str(paths.out_log()),
            'StandardErrorPath': str(paths.err_log()),
            'EnvironmentVariables': env}


def write_plist() -> Path:
    paths.ensure_runtime()
    path = paths.plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(build_plist()))
    return path


def is_loaded() -> bool:
    return run_launchctl('print', target()).returncode == 0


def health(port: int | None = None, timeout: float = 1.0) -> dict | None:
    """O corpo do /health, ou None se o servidor não responde."""
    req = urllib.request.Request(f'http://127.0.0.1:{port or paths.active_port()}/health',
                                 headers={'Authorization': f'Bearer {paths.read_token()}'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def wait_health(timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    port = paths.default_port()  # o agente sobe com o ambiente de quem o carregou, e o mcp.json velho não vale
    while True:
        data = health(port)
        if data or time.monotonic() >= deadline:
            return data
        time.sleep(0.1)


def check_python():
    """O agente fixa o interpretador que roda o `start`, e o servidor pede o 3.10."""
    if sys.version_info < (3, 10):
        v = '.'.join(str(n) for n in sys.version_info[:3])
        raise RuntimeError(f'o brza mcp pede Python 3.10 ou mais, e {sys.executable} é o {v}; rode com um python3 mais novo')


def start(timeout: float = 15.0) -> dict:
    """Grava o plist, carrega o agente e espera o /health. Levanta RuntimeError se algo falha. Agente já carregado é
    descarregado antes: o `kickstart -k` não relê o plist."""
    check_python()
    paths.ensure_token()
    plist = write_plist()
    if is_loaded():
        run_launchctl('bootout', target())
    for attempt in range(5):  # o bootout termina assíncrono: o bootstrap logo depois pode falhar com erro 5
        r = run_launchctl('bootstrap', domain(), str(plist))
        if r.returncode == 0:
            break
        time.sleep(0.3)
    if r.returncode != 0:
        raise RuntimeError(f'o launchctl falhou ({r.returncode}): {(r.stderr or r.stdout).strip()}')
    data = wait_health(timeout)
    if not data:
        raise RuntimeError(f'o /health não respondeu em {timeout:g}s; veja {paths.err_log()}')
    return data


def stop() -> bool:
    """Descarrega o agente; False se ele não estava carregado."""
    if not is_loaded():
        return False
    r = run_launchctl('bootout', target())
    if r.returncode != 0:
        raise RuntimeError(f'o launchctl falhou ({r.returncode}): {(r.stderr or r.stdout).strip()}')
    return True
