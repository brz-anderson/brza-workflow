"""Onde cada coisa mora: a base (~/.brza, ou a BRZA_HOME), o runtime, o token e o agente do launchd."""
from __future__ import annotations

import json
import os
import plistlib
import secrets
import sys
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


def _brza_root():
    """O módulo brza_root dos hooks do núcleo, que acha o workspace do cwd e lê o TOML no 3.9 e no 3.10."""
    hooks = Path(__file__).resolve().parents[3] / 'core' / '.claude' / 'hooks'
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    import brza_root
    return brza_root


def workspace_root() -> Path | None:
    """O workspace: a BRZA_WORKSPACE, ou o do cwd pelo brza_root; None fora de um."""
    env = os.environ.get('BRZA_WORKSPACE')
    if env:
        return Path(env).expanduser()
    try:
        found = _brza_root().find(Path.cwd())
    except Exception:  # noqa: BLE001 - sem workspace legível, a porta cai no padrão
        return None
    return found.root if found else None


def _read_toml(path: Path) -> dict:
    text = path.read_text()
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return _brza_root().parse_toml(text)
    return tomllib.loads(text)


class TomlUnreadable(SystemExit):
    """O brza.toml que não se lê: o `serve` o trata como "sem a chave"; os outros pontos de entrada o recusam."""


def _check_port(value, origin: str, low: int = 1) -> int:
    """A porta válida (inteiro de 1 a 65535, sem booleano) ou SystemExit apontando a origem."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= 65535:
        raise SystemExit(f'{origin}: a porta precisa ser um inteiro de 1 a 65535, e veio {value!r}')
    return value


def workspace_port(root: Path | None = None) -> int | None:
    """O `[mcp] port` do <workspace>/brza.toml (o `root`, ou o do ambiente), ou None sem workspace, sem arquivo ou sem
    a chave. Arquivo ilegível ou porta que não é inteiro de 1 a 65535 recusam, com o caminho."""
    root = root if root is not None else workspace_root()
    if root is None or not (root / 'brza.toml').is_file():
        return None
    path = root / 'brza.toml'
    try:
        port = _read_toml(path).get('mcp', {}).get('port')
    except Exception as e:  # noqa: BLE001
        raise TomlUnreadable(f'{path} ilegível: {e}')
    return None if port is None else _check_port(port, f'{path} [mcp] port')


def default_port(port: int | None = None, lenient: bool = False) -> int:
    """A porta: o `--port` (o argumento), a BRZA_MCP_PORT, o `[mcp] port` do brza.toml do workspace, ou 4242. Valor
    inválido recusa; com `lenient`, o brza.toml ilegível conta como "sem a chave" e avisa no stderr (o `serve` do
    launchd não pode sair em laço por um erro de digitação)."""
    if port is not None:
        return _check_port(port, '--port', low=0)  # 0: a porta efêmera
    if os.environ.get('BRZA_MCP_PORT'):
        text = os.environ['BRZA_MCP_PORT']
        try:
            value = int(text)
        except ValueError:
            raise SystemExit(f'BRZA_MCP_PORT: a porta precisa ser um inteiro de 1 a 65535, e veio {text!r}')
        return _check_port(value, 'BRZA_MCP_PORT')
    try:
        return workspace_port() or DEFAULT_PORT
    except TomlUnreadable as e:
        if not lenient:
            raise
        print(f'{e}; sem a chave [mcp] port, vale a porta {DEFAULT_PORT}', file=sys.stderr, flush=True)
        return DEFAULT_PORT


def _plist_env() -> dict:
    try:
        env = plistlib.loads(plist_path().read_bytes()).get('EnvironmentVariables')
    except Exception:  # noqa: BLE001 - sem plist legível, vale o resto da cadeia
        return {}
    return env if isinstance(env, dict) else {}


def active_port() -> int:
    """A porta do servidor que já roda, para `status` e `config` fora do workspace: o `mcp.json` que o `serve` grava,
    depois o agente carregado (`BRZA_MCP_PORT` ou o `[mcp] port` do `BRZA_WORKSPACE` dele) e, por fim, o `default_port`."""
    try:
        port = json.loads(info_path().read_text()).get('port')
        if isinstance(port, int) and not isinstance(port, bool):
            return port
    except (OSError, ValueError, AttributeError):
        pass
    env = _plist_env()
    try:
        if env.get('BRZA_MCP_PORT'):
            return int(env['BRZA_MCP_PORT'])
    except (TypeError, ValueError):
        pass
    if env.get('BRZA_WORKSPACE'):
        try:
            port = workspace_port(Path(env['BRZA_WORKSPACE']).expanduser())
        except SystemExit:  # a sonda não recusa: vale o resto da cadeia
            port = None
        if port:
            return port
    try:
        return default_port(lenient=True)
    except SystemExit:
        return DEFAULT_PORT


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
