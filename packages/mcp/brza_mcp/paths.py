"""Onde cada coisa mora: a base (~/.brza, ou a BRZA_HOME), o runtime, o token e o agente do launchd."""
import os
import secrets
from pathlib import Path

from . import DEFAULT_PORT, LABEL


def brza_home() -> Path:
    return Path(os.environ.get('BRZA_HOME') or Path.home() / '.brza').expanduser()


def runtime_dir() -> Path:
    return brza_home() / 'runtime'


def token_path() -> Path:
    return runtime_dir() / 'mcp.token'


def info_path() -> Path:
    return runtime_dir() / 'mcp.json'


def out_log() -> Path:
    return runtime_dir() / 'mcp.out.log'


def err_log() -> Path:
    return runtime_dir() / 'mcp.err.log'


def launch_agents_dir() -> Path:
    return Path(os.environ.get('BRZA_LAUNCH_AGENTS_DIR') or Path.home() / 'Library' / 'LaunchAgents').expanduser()


def plist_path() -> Path:
    return launch_agents_dir() / f'{LABEL}.plist'


def default_port() -> int:
    """A 4242, ou a BRZA_MCP_PORT (para teste e para quem tem a porta ocupada)."""
    return int(os.environ.get('BRZA_MCP_PORT') or DEFAULT_PORT)


def ensure_runtime() -> Path:
    runtime_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    return runtime_dir()


def read_token() -> str:
    try:
        return token_path().read_text().strip()
    except FileNotFoundError:
        return ''


def ensure_token() -> str:
    """O token Bearer: o que já existe, ou um novo, criado com 0600 já na abertura (sem janela com outra permissão)."""
    ensure_runtime()
    token = read_token()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(token_path(), flags, 0o600)
    except FileExistsError:
        if read_token():
            return read_token()
        token_path().unlink()  # arquivo vazio: recria
        fd = os.open(token_path(), flags, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token + '\n')
    return token
