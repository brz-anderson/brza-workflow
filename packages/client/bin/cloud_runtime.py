"""CloudRuntime: list/read do degrau 1 e o turno `open`/`send` pelo CLI `agent`.

`list` e `read` vêm do PR #3. `open` e `send` entram no conjunto só quando há chave
(`CURSOR_API_KEY` ou `BRZA_CURSOR_API_KEY`) e o binário `agent`. Sem um dos dois, a
chamada falha por `require_capability` e nomeia o runtime e a capacidade.

`open(spec)` faz o primeiro `agent -p`, sem `--resume` e sem `create-chat`. O id é o
`session_id` do JSON. `send(session_id, prompt)` acrescenta `--resume` e relê modelo,
modo, persona e workspace do `persona.json` no home da persona.

A chave vai só no filho, como `CURSOR_API_KEY`. O scrub cobre o home da persona e os
arquivos do workspace criados ou modificados no turno. O HOME pai não é tocado. O
`close` apaga o home da persona, salvo `keep_home`.
"""

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from agent_runtime import ABSENT, MissingCapability, SessionRecord, absent_metric, require_capability
from brza_runtime import BrzaError

RUNTIME_NAME = 'cloud'
FORBIDDEN_FLAGS = ('--force', '--yolo', '--api-key')
TOKEN_FIELDS = (
    ('inputTokens', 'input'),
    ('outputTokens', 'output'),
    ('cacheReadTokens', 'cache_read'),
    ('cacheWriteTokens', 'cache_write'),
    ('reasoningTokens', 'reasoning'),
)
META_NAME = 'persona.json'
HOME_PARTS = ('.local', 'share', 'brza', 'cloud-runtime')
CACHE_NAME = 'cloud-runtime-cache'
CACHE_DIRS = (
    '.npm',
    '.bun',
    '.cache',
    os.path.join('.cursor', 'plugins'),
    os.path.join('.cursor', 'skills-cursor'),
    os.path.join('.local', 'share', 'cursor-agent'),
)


class CloudTurnError(BrzaError):
    """O CLI `agent` falhou, estourou o tempo ou devolveu erro. `returncode` sai 1."""

    def __init__(self, message):
        super().__init__(message)
        self.returncode = 1


class Turn:
    """O que `open` e `send` devolvem. `session_id` é o id do `--resume`."""

    def __init__(self, session_id, result, duration_ms, usage, measurement):
        self.session_id = session_id
        self.result = result
        self.duration_ms = duration_ms
        self.usage = usage
        self.measurement = measurement


class CloudRuntime:
    name = RUNTIME_NAME

    def __init__(self, env=None, log_path=None, timeout_s=180, keep_home=False):
        self.env = os.environ if env is None else env
        self.log_path = _default_log(self.env) if log_path is None else log_path
        self.timeout_s = timeout_s
        self.keep_home = keep_home
        self._homes = {}
        self._sessions = {}
        self.last_measurement = None

    def capabilities(self):
        declared = {'list', 'read'}
        if self._key() and self._binary():
            declared.update(('open', 'send'))
        return frozenset(declared)

    def list(self):
        require_capability(self, 'list')
        cid = (self.env.get('CURSOR_CONVERSATION_ID') or '').strip()
        if not cid:
            raise BrzaError('cloud: CURSOR_CONVERSATION_ID ausente; nada foi listado')
        return [SessionRecord(id=cid, source='cloud', agent='cursor', title=None, status='running',
                              started_at=None, metrics={'cost_usd': absent_metric(), 'tokens': absent_metric()})]

    def read(self, session_id=None):
        """O transcript ainda não está na máquina. O campo sai `ausente`, e nada é lido do Studio."""
        require_capability(self, 'read')
        cid = (self.env.get('CURSOR_CONVERSATION_ID') or '').strip()
        return {'id': session_id or cid or None, 'source': 'cloud', 'transcript': ABSENT}

    def usage(self, session_id):
        require_capability(self, 'usage')
        raise BrzaError('cloud: usage sem implementação')

    def open(self, spec):
        """Primeiro turno. `spec` traz persona, model, workspace, mode e prompt. Devolve `Turn`."""
        self._require_turn('open')
        if not isinstance(spec, dict):
            raise CloudTurnError('cloud: open recebe um spec com persona, model, workspace, mode e prompt.')
        missing = [name for name in ('persona', 'model', 'workspace', 'prompt') if not spec.get(name)]
        if missing:
            raise CloudTurnError('cloud: open sem ' + ', '.join(missing) + '.')
        return self._turn(capability='open', persona=spec['persona'], model=spec['model'],
                          workspace=spec['workspace'], mode=spec.get('mode') or 'agent',
                          session=None, prompt=spec['prompt'])

    def send(self, session_id, prompt):
        """Turno seguinte. O `session_id` do JSON do `open` vai em `--resume`."""
        self._require_turn('send')
        if not isinstance(session_id, str) or not session_id:
            raise CloudTurnError('cloud: send precisa do session_id devolvido pelo open.')
        meta = self._meta_for(session_id)
        return self._turn(capability='send', persona=meta['persona'], model=meta['model'],
                          workspace=meta['workspace'], mode=meta.get('mode') or 'agent',
                          session=session_id, prompt=prompt)

    def override_meta(self, session_id, model=None, mode=None):
        """Flags explícitas do CLI vencem o metadado e ficam gravadas para o próximo send."""
        meta = self._meta_for(session_id)
        if model:
            meta['model'] = model
        if mode:
            meta['mode'] = mode
        self._store_meta(meta)
        return meta

    def close(self):
        """Apaga o home da persona. Com `keep_home`, só tira a chave para um send de outro processo."""
        key = self._key()
        for home in list(self._homes.values()):
            if key:
                self._scrub_persona(home, key, refuse_protected=False)
            if not self.keep_home and os.path.lexists(home) and not os.path.islink(home):
                shutil.rmtree(home, ignore_errors=True)
        self._homes.clear()
        self._sessions.clear()

    def _require_turn(self, capability):
        try:
            require_capability(self, capability)
        except MissingCapability:
            raise MissingCapability(self.name, capability, self._turn_hint()) from None

    def _turn_hint(self):
        missing = []
        if not self._key():
            missing.append('defina BRZA_CURSOR_API_KEY ou CURSOR_API_KEY no ambiente do processo')
        if not self._binary():
            missing.append(
                'instale o binário agent com `curl https://cursor.com/install -fsS | bash` '
                'e deixe `agent` no PATH')
        return ' '.join(missing) + '.' if missing else ''

    def _key(self):
        for name in ('CURSOR_API_KEY', 'BRZA_CURSOR_API_KEY'):
            value = self.env.get(name)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _binary(self):
        return shutil.which('agent', path=self.env.get('PATH'))

    def _parent_home(self):
        return self.env.get('HOME') or os.path.expanduser('~')

    def _home_for(self, persona):
        label = _safe_name(persona)
        cached = self._homes.get(label)
        if cached and os.path.isdir(cached) and not os.path.islink(cached):
            return cached
        current = self._parent_home()
        for part in (*HOME_PARTS, label):
            current = os.path.join(current, part)
            if os.path.islink(current):
                raise CloudTurnError(
                    f'cloud: {current} é um symlink. O home da persona não segue link a partir de .local.')
            if not os.path.isdir(current):
                os.mkdir(current, 0o700)
        os.chmod(current, 0o700)
        self._homes[label] = current
        return current

    def _shared_cache(self):
        path = os.path.join(self._parent_home(), '.local', 'share', 'brza', CACHE_NAME)
        if os.path.islink(path):
            raise CloudTurnError(f'cloud: {path} é um symlink. O cache compartilhado não segue link.')
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _share_caches(self, home):
        """A instalação e o cache do CLI ficam fora do home. A medição mostrou 1,2G contra um auth.json."""
        shared = self._shared_cache()
        install = os.path.join(self._parent_home(), '.local', 'share', 'cursor-agent')
        dest = os.path.join(home, '.local', 'share', 'cursor-agent')
        if os.path.isdir(install) and not os.path.islink(install) and not os.path.lexists(dest):
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
            os.symlink(install, dest)
        os.makedirs(os.path.join(shared, 'xdg'), mode=0o700, exist_ok=True)

    def _relocate_caches(self, home):
        shared = self._shared_cache()
        for relative in CACHE_DIRS:
            source = os.path.join(home, relative)
            if not os.path.isdir(source) or os.path.islink(source):
                continue
            dest = os.path.join(shared, relative.replace(os.sep, '-'))
            if os.path.exists(dest):
                shutil.rmtree(source, ignore_errors=True)
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(source, dest)
            if not os.path.lexists(source):
                os.makedirs(os.path.dirname(source), exist_ok=True)
                os.symlink(dest, source)

    def _child_env(self, key, home):
        env = dict(self.env)
        env.pop('BRZA_CURSOR_API_KEY', None)
        env['HOME'] = home
        env['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
        env['XDG_CACHE_HOME'] = os.path.join(self._shared_cache(), 'xdg')
        env['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
        env['npm_config_cache'] = os.path.join(self._shared_cache(), 'npm')
        env['BUN_INSTALL_CACHE_DIR'] = os.path.join(self._shared_cache(), 'bun')
        env['CURSOR_API_KEY'] = key
        return env

    def _argv(self, binary, model, workspace, mode, session, prompt):
        argv = [binary, '-p', '--output-format', 'json']
        if model:
            argv.extend(('--model', model))
        if workspace:
            argv.extend(('--workspace', os.path.abspath(workspace)))
        argv.append('--trust')
        if mode not in (None, '', 'agent'):
            argv.extend(('--mode', mode))
        if session:
            argv.extend(('--resume', session))
        found = [flag for flag in FORBIDDEN_FLAGS if flag in argv]
        if found:
            raise CloudTurnError('cloud: o argv do agent não leva ' + ', '.join(found) + '.')
        argv.append('--')
        argv.append(prompt)
        return argv

    def _turn(self, capability, persona, model, workspace, mode, session, prompt):
        if not isinstance(prompt, str) or not prompt:
            raise CloudTurnError('cloud: o turno precisa de um prompt.')
        key = self._key()
        binary = self._binary()
        home = self._home_for(persona)
        self._share_caches(home)
        self._scrub_persona(home, key, refuse_protected=True)
        before = _snapshot(workspace)
        argv = self._argv(binary, model, workspace, mode, session, prompt)
        env = self._child_env(key, home)
        started = time.monotonic()
        timed_out = False
        proc = None
        stdout, stderr, returncode = '', '', 1
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout_s)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _reap(proc)
                stdout, stderr = proc.communicate()
                returncode = proc.returncode if proc.returncode is not None else 1
            except BaseException:
                _reap(proc)
                raise
            stdout = stdout or ''
            stderr = stderr or ''
        finally:
            _reap(proc)
            _close_pipes(proc)
            self._scrub_persona(home, key, refuse_protected=False)
            self._scrub_workspace(workspace, key, before)
            self._scrub_owned(self._shared_cache(), key)
            self._relocate_caches(home)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = _parse_payload(stdout)
        usage = payload.get('usage') if isinstance(payload, dict) else None
        session_id = payload.get('session_id') if isinstance(payload, dict) else None
        if not isinstance(session_id, str) or not session_id:
            session_id = None
        if isinstance(payload, dict) and 'duration_ms' in payload:
            duration = _number(payload.get('duration_ms'))
        else:
            duration = elapsed_ms
        is_error = _is_error(payload)
        measurement = self._record(
            capability=capability, persona=persona, model=model, session_id=session_id,
            duration_ms=duration, usage=usage, is_error=is_error or timed_out or returncode != 0, key=key)
        if timed_out:
            raise CloudTurnError(f'cloud: o agent excedeu o timeout de {self.timeout_s}s no {capability}.')
        if payload is None:
            detail = _redact((stderr or stdout or '').strip(), key)[:500]
            raise CloudTurnError(f'cloud: o agent não devolveu JSON. {detail}'.rstrip())
        if is_error or returncode != 0:
            detail = _redact(str(payload.get('result') or stderr or ''), key)[:500]
            why = 'is_error' if is_error else f'código {returncode}'
            raise CloudTurnError(f'cloud: o agent falhou no {capability} ({why}). {detail}'.rstrip())
        if session_id is None:
            raise CloudTurnError('cloud: o agent não devolveu session_id.')
        result = _redact(str(payload.get('result') or ''), key)
        meta = {
            'persona': persona, 'model': model, 'workspace': os.path.abspath(workspace),
            'mode': mode or 'agent', 'session_id': session_id,
        }
        self._store_meta(meta, home)
        return Turn(session_id, result, duration, tokens_of(usage), measurement)

    def _record(self, capability, persona, model, session_id, duration_ms, usage, is_error, key):
        line = {
            'event': 'execution',
            'runtime': RUNTIME_NAME,
            'agent': 'cursor',
            'source': 'cli',
            'timestamp': datetime.now().astimezone().isoformat(timespec='seconds'),
            'capability': capability,
            'persona': _redact(str(persona), key),
            'model': model,
            'session_id': session_id,
            'duration_ms': duration_ms,
            'tokens': tokens_of(usage),
            'estimated_cost_usd': ABSENT,
            'is_error': is_error,
        }
        self.last_measurement = line
        if self.log_path:
            path = Path(self.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + '\n')
        return line

    def _meta_for(self, session_id):
        remembered = self._sessions.get(session_id)
        if remembered:
            return dict(remembered)
        for home in self._known_homes():
            path = os.path.join(home, META_NAME)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            try:
                meta = json.loads(Path(path).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get('session_id') == session_id:
                label = _safe_name(meta.get('persona'))
                self._homes[label] = home
                self._sessions[session_id] = meta
                return dict(meta)
        raise CloudTurnError(
            'cloud: send sem o metadado deste session_id. O close apaga o home da persona; '
            'use keep_home para um send de outro processo.')

    def _known_homes(self):
        homes = list(self._homes.values())
        root = os.path.join(self._parent_home(), *HOME_PARTS)
        if os.path.isdir(root) and not os.path.islink(root):
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if os.path.isdir(path) and not os.path.islink(path) and path not in homes:
                    homes.append(path)
        return homes

    def _store_meta(self, meta, home=None):
        clean = {key: value for key, value in meta.items() if key != 'key'}
        self._sessions[clean['session_id']] = dict(clean)
        home = home or self._homes.get(_safe_name(clean.get('persona')))
        if not home:
            return
        path = os.path.join(home, META_NAME)
        Path(path).write_text(json.dumps(clean, ensure_ascii=False), encoding='utf-8')
        os.chmod(path, 0o600)

    def _protected(self, path, workspace):
        parent = _real(self._parent_home())
        target = _real(path)
        allowed = [_real(home) for home in self._homes.values()]
        allowed.append(_real(self._shared_cache()))
        if workspace:
            allowed.append(_real(workspace))
        if any(target == root or target.startswith(root + os.sep) for root in allowed if root):
            return False
        return bool(parent) and (target == parent or target.startswith(parent + os.sep))

    def _scrub_persona(self, home, key, refuse_protected):
        if not key or not home:
            return
        _scrub_tree(home, key, self, workspace=None, refuse_protected=refuse_protected, touched=None)

    def _scrub_workspace(self, workspace, key, before):
        if not key or not workspace:
            return
        _scrub_tree(workspace, key, self, workspace=workspace, refuse_protected=False,
                    touched=lambda path: _touched(path, before))

    def _scrub_owned(self, root, key):
        if not key or not root:
            return
        _scrub_tree(root, key, self, workspace=root, refuse_protected=False, touched=None)


def open_argv(model=None, workspace=None, chat_id=None):
    """O argv que o dry-run imprime. Sem `--force` e sem `create-chat`.

    Sem `chat_id`, é o primeiro turno: o processo imprime um JSON e o id é o `session_id`.
    Com `chat_id`, o turno seguinte acrescenta `--resume`.
    """
    argv = ['agent', '-p', '--output-format', 'json', '--model', model or '<id>',
            '--workspace', str(workspace) if workspace else '<dir>', '--trust']
    if chat_id:
        argv += ['--resume', chat_id]
    return argv


def _safe_name(name):
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(name or 'persona')).strip('-')
    return (cleaned or 'persona')[:40]


def _default_log(env):
    root = env.get('BRZA_WORKSPACE')
    if not root:
        return None
    return os.path.join(root, '.measurements', 'measurements.jsonl')


def _redact(text, key):
    if not text or not key:
        return text or ''
    return str(text).replace(key, '[redacted]')


def _number(value):
    """Inteiro medido, inclusive 0. Float integral vira int. O resto sai ausente."""
    if isinstance(value, bool):
        return ABSENT
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return ABSENT
    return ABSENT


def tokens_of(usage):
    usage = usage if isinstance(usage, dict) else {}
    return {name: _number(usage.get(raw)) if raw in usage else ABSENT for raw, name in TOKEN_FIELDS}


def _is_error(payload):
    if not isinstance(payload, dict) or 'is_error' not in payload:
        return False
    flag = payload.get('is_error')
    if isinstance(flag, bool):
        return flag
    return True


def _real(path):
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _snapshot(root):
    found = {}
    if not root or not os.path.isdir(root) or os.path.islink(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            found[path] = (st.st_mtime_ns, st.st_size, st.st_mode)
    return found


def _touched(path, before):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    previous = before.get(path)
    if previous is None:
        return True
    return (st.st_mtime_ns, st.st_size) != previous[:2]


def _scrub_tree(root, key, runtime, workspace, refuse_protected, touched):
    if not root or not os.path.isdir(root) or os.path.islink(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if touched is not None and not touched(path):
                continue
            _scrub_path(path, key, runtime, workspace, refuse_protected)


def _scrub_path(path, key, runtime, workspace, refuse_protected):
    try:
        st = os.lstat(path)
    except OSError:
        return
    if stat.S_ISLNK(st.st_mode):
        _scrub_link(path, key, runtime, workspace, refuse_protected)
        return
    if not stat.S_ISREG(st.st_mode):
        return
    if _contains_key(path, key):
        _remove(path)


def _scrub_link(path, key, runtime, workspace, refuse_protected):
    try:
        target = os.path.realpath(path)
    except OSError:
        _remove(path)
        return
    if not _contains_key(target, key):
        return
    if runtime._protected(target, workspace):
        if refuse_protected:
            raise CloudTurnError(
                'cloud: o arquivo da chave é um link para o HOME pai. O turno foi recusado e o alvo ficou intacto.')
        return
    if _regular(target):
        _remove(target)
    _remove(path)


def _contains_key(path, key):
    if not _regular(path):
        return False
    try:
        with open(path, 'rb') as handle:
            blob = handle.read()
    except OSError:
        return False
    return key.encode('utf-8') in blob


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_payload(stdout):
    text = (stdout or '').strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                parsed = row
    if isinstance(parsed, dict):
        return parsed
    return None


def _reap(proc):
    """Mata o grupo do filho direto. Um neto que chamou setsid escapa: limitação conhecida."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _close_pipes(proc):
    if proc is None:
        return
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _print_turn(turn):
    measurement = turn.measurement
    print(json.dumps({
        'rc': 0,
        'session_id': turn.session_id,
        'result': turn.result,
        'duration_ms': turn.duration_ms,
        'tokens': measurement['tokens'],
        'estimated_cost_usd': measurement['estimated_cost_usd'],
    }, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='cloud_runtime')
    sub = parser.add_subparsers(dest='command', required=True)
    shared = dict(type=float, default=180)
    open_cmd = sub.add_parser('open')
    open_cmd.add_argument('--persona', required=True)
    open_cmd.add_argument('--model', required=True)
    open_cmd.add_argument('--workspace', required=True)
    open_cmd.add_argument('--mode', default='agent')
    open_cmd.add_argument('--prompt', required=True)
    open_cmd.add_argument('--timeout', **shared)
    open_cmd.add_argument('--log')
    open_cmd.add_argument('--keep-home', action='store_true')
    send_cmd = sub.add_parser('send')
    send_cmd.add_argument('--session', required=True)
    send_cmd.add_argument('--prompt', required=True)
    send_cmd.add_argument('--model')
    send_cmd.add_argument('--mode')
    send_cmd.add_argument('--timeout', **shared)
    send_cmd.add_argument('--log')
    send_cmd.add_argument('--keep-home', action='store_true')
    args = parser.parse_args(argv)
    runtime = CloudRuntime(timeout_s=args.timeout, log_path=args.log, keep_home=args.keep_home)
    try:
        if args.command == 'open':
            turn = runtime.open({
                'persona': args.persona, 'model': args.model, 'workspace': args.workspace,
                'mode': args.mode, 'prompt': args.prompt,
            })
        else:
            if args.model or args.mode:
                runtime.override_meta(args.session, model=args.model, mode=args.mode)
            turn = runtime.send(args.session, args.prompt)
    except (CloudTurnError, BrzaError) as exc:
        code = getattr(exc, 'returncode', 1)
        print(f'rc={code} {exc}', file=sys.stderr)
        return code
    finally:
        runtime.close()
    _print_turn(turn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
