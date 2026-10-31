"""O CLI e o runtime do BrzA Studio, para os scripts de `scripts/brza/`: o envio ao chat, o git, o worktree do
brza-studio, o workstream de um plano, a memória do workstream e o `--agents` do claude.

O app ainda usa os nomes do Orca por dentro: o arquivo de runtime é `orca-runtime.json` e o terminal e o chat recebem
`ORCA_CLI_COMMAND` e `ORCA_USER_DATA_PATH`. Dentro do app, esses valores vêm do ambiente; fora dele, valem os caminhos
do BrzA Studio instalado em /Applications.
"""

import hashlib
import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path

APP_BIN = Path('/Applications/BrzA Studio.app/Contents/Resources/bin')
DEFAULT_CLI = APP_BIN / 'brza'
DEFAULT_USER_DATA = Path.home() / 'Library' / 'Application Support' / 'brza-studio'
WORKSPACE = Path(os.environ.get('BRZA_WORKSPACE') or Path.home() / 'Projects' / 'brza-workspace')
STUDIO = Path(os.environ.get('BRZA_STUDIO') or Path.home() / 'Projects' / 'brza-studio')
LOCAL_BIN = Path.home() / '.local' / 'bin'


class BrzaError(RuntimeError):
    """O CLI ou o runtime respondeu com erro, ou não respondeu."""


def cli_command() -> str:
    return os.environ.get('ORCA_CLI_COMMAND') or str(DEFAULT_CLI)


def user_data() -> Path:
    return Path(os.environ.get('ORCA_USER_DATA_PATH') or DEFAULT_USER_DATA)


def runtime_file() -> Path:
    return user_data() / 'orca-runtime.json'


def brza(*args):
    """Roda o CLI com `--json` e devolve o `result`; erro vira BrzaError."""
    r = subprocess.run([cli_command(), *args, '--json'], capture_output=True, text=True)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise BrzaError(f'brza {" ".join(args)} falhou: {r.stderr.strip() or r.stdout.strip()}') from None
    if not out.get('ok', False):
        raise BrzaError(f'brza {" ".join(args)} falhou: {json.dumps(out.get("error", out), ensure_ascii=False)}')
    return out['result']


def rpc(method, params=None, timeout: float = 10.0):
    """Chama um método do runtime pelo socket local, como o CLI faz; o CLI não expõe as configurações."""
    try:
        meta = json.loads(runtime_file().read_text())
        endpoint = next(t['endpoint'] for t in meta['transports'] if t['kind'] == 'unix')
    except (OSError, ValueError, KeyError, StopIteration) as e:
        raise BrzaError(f'não li o runtime em {runtime_file()} ({e})') from None
    request = {'id': str(uuid.uuid4()), 'authToken': meta['authToken'], 'method': method, 'params': params}
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(endpoint)
            s.sendall((json.dumps(request) + '\n').encode())
            buffer = b''
            while b'\n' not in buffer:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buffer += chunk
    except OSError as e:
        raise BrzaError(f'{method}: o runtime não respondeu ({e})') from None
    try:
        out = json.loads(buffer.split(b'\n')[0])
    except ValueError:
        raise BrzaError(f'{method}: resposta ilegível do runtime') from None
    if not out.get('ok', False):
        raise BrzaError(f'{method}: {json.dumps(out.get("error", out), ensure_ascii=False)}')
    return out['result']


def canonicalize(value) -> str:
    """O canonicalize do brza-studio (src/shared/structured-agent-session-mutation.ts), que a impressão digital cobre."""
    if isinstance(value, list):
        return '[' + ','.join(canonicalize(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(f'{json.dumps(k, ensure_ascii=False)}:{canonicalize(v)}'
                              for k, v in sorted(value.items())) + '}'
    return json.dumps(value, ensure_ascii=False)


def send_mutation(session_id: str, text: str, fence: int, now_ms: int, entropy: str) -> dict:
    """Os parâmetros do agentSession.send, como os monta structuredAgentSessionMessageSendMutation no brza-studio."""
    body = {'kind': 'message', 'role': 'user', 'blocks': [{'type': 'text', 'text': text}]}
    fields = {'body': body, 'delivery': 'queue-if-active'}
    payload = canonicalize({'method': 'agentSession.send', 'sessionId': session_id, 'fields': fields})
    return {'envelope': {'sessionId': session_id, 'clientOperationId': f'{now_ms}-{entropy}',
                         'expectedRuntimeFence': fence,
                         'payloadFingerprint': hashlib.sha256(payload.encode()).hexdigest()},
            **fields}


def send_to_chat(session_id: str, text: str):
    """Manda uma mensagem ao chat, que espera na fila com o agente no meio de um turno."""
    page = rpc('agentSession.history', {'sessionId': session_id, 'direction': 'tail', 'limit': 1})['page']
    rpc('agentSession.send', send_mutation(session_id, text, page['fence'], int(time.time() * 1000),
                                           uuid.uuid4().hex))


def git(*args, cwd: Path | None = None) -> str:
    """Roda o git no `cwd`, ou no checkout do brza-studio, e devolve o stdout; erro vira BrzaError com o stderr."""
    r = subprocess.run(['git', '-C', str(cwd or STUDIO), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise BrzaError(f'git {" ".join(args)} falhou: {r.stderr.strip() or r.stdout.strip()}')
    return r.stdout


def worktree_add(path: Path, base: str = 'origin/master', branch: str | None = None, fetch: bool = True) -> Path:
    """`git fetch origin` (se fetch), e `git worktree add` em `path` de `base`; sem `branch`, sai detached."""
    if fetch:
        git('fetch', 'origin')
    git('worktree', 'add', *(('-b', branch) if branch else ('--detach',)), str(path), base)
    return path


def install_offline(path: Path):
    """`pnpm install --offline --frozen-lockfile` em `path`; falha vira BrzaError que diz o comando online."""
    r = subprocess.run(['pnpm', 'install', '--offline', '--frozen-lockfile'], cwd=path, capture_output=True, text=True)
    if r.returncode != 0:
        tail = '\n'.join((r.stderr.strip() or r.stdout.strip()).splitlines()[-5:])
        raise BrzaError(f'o install offline falhou em {path}; rode lá `pnpm install --frozen-lockfile`\n{tail}')


def worktree_remove(path: Path):
    """`git worktree remove` sem --force; usado no worktree temporário, que nasce limpo."""
    git('worktree', 'remove', str(path))


# O workstream e a memória dele

WORKSTREAM_PREFIX = 'workstream/'
STUDIO_BRANCH = re.compile(r'[a-z]+/pln-(\d+)(-|$)')


class Refused(Exception):
    """O pedido não se cumpre, e a mensagem diz por quê; o brza-note a imprime como `recusado:` e sai com 1."""


def workstream_root(name: str) -> Path:
    """O worktree do workstream `name` em <workspace>/.worktrees; sem ele, recusa e diz o comando que o cria."""
    root = WORKSPACE / '.worktrees' / name
    if not (root / '.git').exists():
        raise Refused(f'o workstream {name} não tem worktree em {root}; crie com `brza note workstream {name}`')
    return root


def workstreams_with_plan(number: str, refs: str) -> list[str]:
    """Os nomes dos workstreams, entre as branches de `refs` (refs/heads/workstream ou refs/remotes/origin/workstream),
    que têm plans/PLN-<number>-* — um git ls-tree por ref."""
    prefix = f'plans/PLN-{number}-'
    base = f'{refs}/'
    found = []
    for ref in git('for-each-ref', '--format=%(refname)', base, cwd=WORKSPACE).split():
        names = git('ls-tree', '--name-only', '-z', ref, 'plans/', cwd=WORKSPACE).split('\0')
        if any(n.startswith(prefix) for n in names):
            found.append(ref.removeprefix(base))
    return sorted(found)


def workstream_of_plan(number: str) -> str:
    """O workstream que tem o plano PLN-<number>: as branches locais primeiro e, sem achar nenhuma, as de origin. Nenhum
    ou mais de um recusa e pede o --workstream."""
    for refs in ('refs/heads/workstream', 'refs/remotes/origin/workstream'):
        found = workstreams_with_plan(number, refs)
        if len(found) == 1:
            return found[0]
        if found:
            raise Refused(f'o PLN-{number} está em mais de um workstream ({", ".join(found)}); '
                          f'passe --workstream <nome>')
    raise Refused(f'o PLN-{number} não está em nenhum workstream; passe --workstream <nome>')


def set_auto_memory(root: Path, memory: Path) -> bool:
    """Grava `autoMemoryDirectory` = `memory` (absoluto) em <root>/.claude/settings.local.json, com merge: as outras
    chaves ficam. Devolve se o arquivo mudou. JSON ilegível ou que não é objeto vira BrzaError, sem sobrescrever."""
    path = root / '.claude' / 'settings.local.json'
    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text() or '{}')
        except ValueError as e:
            raise BrzaError(f'{path} não é JSON legível ({e}); conserte à mão') from None
        if not isinstance(settings, dict):
            raise BrzaError(f'{path} não é um objeto JSON; conserte à mão')
    if settings.get('autoMemoryDirectory') == str(memory):
        return False
    settings['autoMemoryDirectory'] = str(memory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
    return True


def setup_memory(tree: Path) -> bool:
    """A memória da sessão do worktree de workstream `tree`: cria <tree>/memory/, vazio e sem commit, e aponta o
    `autoMemoryDirectory` do settings.local.json para ele. Devolve se algo mudou; idempotente."""
    tree = tree.resolve()
    memory = tree / 'memory'
    created = not memory.is_dir()
    memory.mkdir(exist_ok=True)
    return set_auto_memory(tree, memory) or created


# O --agents do claude

# A ordem das chaves de cada agente no JSON; as outras vêm depois, em ordem alfabética.
AGENT_KEYS = ('description', 'prompt', 'model', 'tools', 'memory', 'effort')
# No frontmatter do Claude, estas chaves aceitam a lista separada por vírgula; no --agents, são array.
AGENT_LIST_KEYS = ('tools', 'disallowedTools', 'skills')


def _scalar(text: str):
    """Um valor YAML simples: o entre aspas, o inteiro, o booleano; o resto, o texto."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        return text[1:-1]
    if text in ('true', 'false'):
        return text == 'true'
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    return text


def parse_frontmatter(text: str) -> tuple[dict, str] | None:
    """O frontmatter YAML simples e o corpo de um .md, ou None sem `---` na primeira linha. Entende `chave: valor`,
    lista inline `[a, b]`, lista em `- item` nas linhas de baixo e o texto em bloco `|` ou `>`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
    except StopIteration:
        return None
    meta: dict = {}
    i = 1
    while i < end:
        m = re.fullmatch(r'([A-Za-z_][\w-]*):\s*(.*)', lines[i].rstrip())
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        rest = []
        while i < end and (not lines[i].strip() or lines[i][0] in ' \t' or lines[i].startswith('- ')):
            rest.append(lines[i])
            i += 1
        items = [r.strip()[2:].strip() for r in rest if r.strip().startswith('- ')]
        if value in ('|', '>', '|-', '>-'):
            parts = [r.strip() for r in rest]
            meta[key] = ('\n' if value[0] == '|' else ' ').join(parts).strip()
        elif value.startswith('[') and value.endswith(']'):
            meta[key] = [_scalar(v) for v in value[1:-1].split(',') if v.strip()]
        elif not value and items:
            meta[key] = [_scalar(v) for v in items]
        elif key in AGENT_LIST_KEYS and ',' in value:
            meta[key] = [v.strip() for v in value.split(',') if v.strip()]
        else:
            meta[key] = _scalar(value)
    for key in AGENT_LIST_KEYS:
        if isinstance(meta.get(key), str):
            meta[key] = [meta[key]]
    return meta, '\n'.join(lines[end + 1:]).strip()


def agents_json(workspace: Path | None = None) -> str | None:
    """O JSON do `claude --agents` com os subagentes de <workspace>/.claude/agents/*.md, na ordem dos nomes:
    {nome: {description, prompt, model, tools?, memory?, effort?, …}}. O nome é o `name` do frontmatter ou o do arquivo;
    o prompt é o corpo. Arquivo sem frontmatter fica de fora. Sem o diretório, ou sem agente, devolve None."""
    folder = Path(workspace or WORKSPACE) / '.claude' / 'agents'
    if not folder.is_dir():
        return None
    agents = {}
    for path in sorted(folder.glob('*.md')):
        parsed = parse_frontmatter(path.read_text())
        if parsed is None:
            continue
        meta, body = parsed
        name = str(meta.pop('name', path.stem))
        agent = {'prompt': body, **meta}
        ordered = [k for k in AGENT_KEYS if k in agent] + sorted(k for k in agent if k not in AGENT_KEYS)
        agents[name] = {k: agent[k] for k in ordered}
    if not agents:
        return None
    return json.dumps(dict(sorted(agents.items())), ensure_ascii=False, separators=(',', ':'))
