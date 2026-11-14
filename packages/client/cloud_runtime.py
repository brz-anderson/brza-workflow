#!/usr/bin/env python3
"""Turnos do `CloudRuntime` pelo CLI `agent`, isolados do degrau 1.

A porta da ADR-003 tem `capabilities`, `list` e `read` obrigatórias nos
adaptadores completos, e `usage`, `open` e `send` opcionais. Capacidade que
não está declarada falha pelo nome do runtime e da capacidade, sem fallback.

Este arquivo só implementa `open` e `send`. O `list` e o `read` do degrau 1
entram em `capabilities` quando o PR A chegar, sem reescrever o turno. O
`brza sessions` do Studio não passa por aqui.

`open` é o primeiro turno: `agent -p --output-format json --model <id>
--workspace <dir> --trust`, com `--mode ask` no revisor. `send` acrescenta
`--resume <chatId>` e repete o workspace, o modelo e o modo do `open`: o CLI
guarda o chat no bucket do workspace. Sem `--force`, sem `--yolo` e sem
`--api-key`. A
chave vai só no ambiente do filho, como `CURSOR_API_KEY`. O `HOME` do filho é
um diretório temporário por persona, reusado no `send` para o resume achar o
estado local; no fim do turno apaga-se qualquer arquivo desse diretório, e o
`auth.json` do HOME do pai, que contenha a chave.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ABSENT = 'ausente'
RUNTIME_NAME = 'cloud'
FORBIDDEN_FLAGS = ('--force', '--yolo', '--api-key')
TOKEN_FIELDS = (
    ('inputTokens', 'input'),
    ('outputTokens', 'output'),
    ('cacheReadTokens', 'cache_read'),
    ('cacheWriteTokens', 'cache_write'),
    ('reasoningTokens', 'reasoning'),
)


class CloudRuntimeError(Exception):
    """Falha do adaptador cloud. `returncode` sai diferente de 0."""

    returncode = 1


class CapabilityError(CloudRuntimeError):
    """A capacidade não está declarada. A mensagem nomeia runtime e capacidade."""

    def __init__(self, runtime, capability, hint):
        self.runtime = runtime
        self.capability = capability
        super().__init__(f'runtime {runtime} não declara a capacidade {capability}. {hint}')


class CloudTurnError(CloudRuntimeError):
    """O CLI `agent` falhou, estourou o tempo ou devolveu `is_error`."""


class Turn:
    """O turno que o `open` e o `send` devolvem. `session_id` é o id do resume."""

    def __init__(self, session_id, result, duration_ms, usage, measurement):
        self.session_id = session_id
        self.result = result
        self.duration_ms = duration_ms
        self.usage = usage
        self.measurement = measurement


class AgentRuntime:
    """Contrato mínimo da porta. Quem não declara a capacidade falha pelo nome."""

    runtime_name = 'agent'

    def capabilities(self):
        return frozenset()

    def require(self, capability):
        if capability not in self.capabilities():
            raise CapabilityError(self.runtime_name, capability, self.capability_hint(capability))

    def capability_hint(self, capability):
        return 'A capacidade não está neste adaptador.'


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
    """Inteiro medido, inclusive 0. `bool` e o que não é inteiro ficam ausentes."""
    if isinstance(value, bool) or not isinstance(value, int):
        return ABSENT
    return value


def tokens_of(usage):
    """Os cinco campos do CLI. Campo que não veio sai `ausente`, nunca 0 inventado."""
    usage = usage if isinstance(usage, dict) else {}
    return {name: _number(usage.get(raw)) if raw in usage else ABSENT for raw, name in TOKEN_FIELDS}


class CloudRuntime(AgentRuntime):
    """`open` e `send` quando há chave e o binário `agent`. Sem um dos dois, não declara."""

    runtime_name = RUNTIME_NAME

    def __init__(self, env=None, log_path=None, timeout_s=180):
        self._env = os.environ if env is None else env
        self.log_path = _default_log(self._env) if log_path is None else log_path
        self.timeout_s = timeout_s
        self._homes = {}
        self._sessions = {}
        self.last_measurement = None

    def capabilities(self):
        if self._key() and self._binary():
            return frozenset({'open', 'send'})
        return frozenset()

    def capability_hint(self, capability):
        if capability not in ('open', 'send'):
            return 'Este módulo só implementa open e send; list, read e usage chegam no PR A.'
        missing = []
        if not self._key():
            missing.append('defina BRZA_CURSOR_API_KEY ou CURSOR_API_KEY no ambiente do processo')
        if not self._binary():
            missing.append(
                'instale o binário agent com `curl https://cursor.com/install -fsS | bash` '
                'e deixe `agent` no PATH')
        return ' '.join(missing) + '.'

    def list(self):
        self.require('list')

    def read(self, session):
        self.require('read')

    def usage(self, session):
        self.require('usage')

    def open(self, persona, model, workspace, mode, prompt):
        """Primeiro turno. Devolve o `Turn`, cujo `session_id` é o id do resume."""
        self.require('open')
        return self._turn(capability='open', persona=persona, model=model, workspace=workspace,
                          mode=mode, session=None, prompt=prompt)

    def send(self, session, prompt, persona=None, model=None, workspace=None, mode=None):
        """Turno seguinte, com `--resume`. `session` é o `session_id` do `open`."""
        self.require('send')
        if not isinstance(session, str) or not session:
            raise CloudTurnError('runtime cloud: send precisa do session_id devolvido pelo open.')
        # O CLI indexa o chat pelo workspace. Sem repetir o do open, o resume abre outro
        # bucket e o modelo não vê o turno anterior.
        remembered = self._sessions.get(session, {})
        if persona is None:
            persona = remembered.get('persona', session)
        if model is None:
            model = remembered.get('model')
        if workspace is None:
            workspace = remembered.get('workspace')
        if mode is None:
            mode = remembered.get('mode')
        return self._turn(capability='send', persona=persona, model=model, workspace=workspace,
                          mode=mode, session=session, prompt=prompt)

    def close(self):
        """Apaga os HOME temporários das personas. A chave já saiu no fim de cada turno."""
        for home in self._homes.values():
            shutil.rmtree(home, ignore_errors=True)
        self._homes.clear()
        self._sessions.clear()

    def _key(self):
        for name in ('CURSOR_API_KEY', 'BRZA_CURSOR_API_KEY'):
            value = self._env.get(name)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _binary(self):
        return shutil.which('agent', path=self._env.get('PATH'))

    def _home_for(self, persona):
        label = _safe_name(persona)
        home = self._homes.get(label)
        if home and os.path.isdir(home):
            return home
        home = tempfile.mkdtemp(prefix=f'brza-cloud-{label}-')
        self._homes[label] = home
        return home

    def _child_env(self, key, home):
        env = dict(self._env)
        env['HOME'] = home
        env['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
        env['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
        env['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
        env['XDG_STATE_HOME'] = os.path.join(home, '.local', 'state')
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
            raise CloudTurnError(
                'runtime cloud: o argv do agent não leva ' + ', '.join(found) + '.')
        argv.append(prompt)
        return argv

    def _turn(self, capability, persona, model, workspace, mode, session, prompt):
        if not isinstance(prompt, str) or not prompt:
            raise CloudTurnError('runtime cloud: o turno precisa de um prompt.')
        key = self._key()
        binary = self._binary()
        home = self._home_for(persona)
        argv = self._argv(binary, model, workspace, mode, session, prompt)
        env = self._child_env(key, home)
        started = time.monotonic()
        timed_out = False
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
                _kill_group(proc.pid)
                stdout, stderr = proc.communicate()
                returncode = proc.returncode
            stdout = stdout or ''
            stderr = stderr or ''
        finally:
            self._scrub(home, key)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = _parse_payload(stdout)
        usage = payload.get('usage') if isinstance(payload, dict) else None
        session_id = payload.get('session_id') if isinstance(payload, dict) else None
        if not isinstance(session_id, str) or not session_id:
            session_id = None
        duration = _number(payload.get('duration_ms')) if isinstance(payload, dict) else ABSENT
        if duration is ABSENT:
            duration = elapsed_ms
        is_error = bool(isinstance(payload, dict) and payload.get('is_error') is True)
        measurement = self._record(
            capability=capability, persona=persona, model=model, session_id=session_id,
            duration_ms=duration, usage=usage, is_error=is_error or timed_out or returncode != 0, key=key)
        if timed_out:
            raise CloudTurnError(
                f'runtime cloud: o agent excedeu o timeout de {self.timeout_s}s no {capability}.')
        if payload is None:
            detail = _redact((stderr or stdout or '').strip(), key)[:500]
            raise CloudTurnError(f'runtime cloud: o agent não devolveu JSON. {detail}'.rstrip())
        if is_error or returncode != 0:
            detail = _redact(str(payload.get('result') or stderr or ''), key)[:500]
            why = 'is_error' if is_error else f'código {returncode}'
            raise CloudTurnError(f'runtime cloud: o agent falhou no {capability} ({why}). {detail}'.rstrip())
        if session_id is None:
            raise CloudTurnError('runtime cloud: o agent não devolveu session_id.')
        result = _redact(str(payload.get('result') or ''), key)
        self._sessions[session_id] = {
            'persona': persona, 'model': model, 'workspace': workspace, 'mode': mode,
        }
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

    def _scrub(self, home, key):
        if not key:
            return
        _scrub_tree(home, key)
        for path in _auth_paths(self._env):
            _scrub_file(path, key)


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


def _auth_paths(env):
    paths = []
    home = env.get('HOME')
    if home:
        paths.append(os.path.join(home, '.config', 'cursor', 'auth.json'))
    xdg = env.get('XDG_CONFIG_HOME')
    if xdg:
        paths.append(os.path.join(xdg, 'cursor', 'auth.json'))
    return paths


def _scrub_tree(root, key):
    if not root or not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for name in filenames:
            _scrub_file(os.path.join(dirpath, name), key)


def _scrub_file(path, key):
    try:
        with open(path, 'rb') as handle:
            blob = handle.read()
    except OSError:
        return
    if key.encode('utf-8') in blob:
        try:
            os.remove(path)
        except OSError:
            pass


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
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
    shared_timeout = dict(type=float, default=180)
    open_cmd = sub.add_parser('open')
    open_cmd.add_argument('--persona', required=True)
    open_cmd.add_argument('--model', required=True)
    open_cmd.add_argument('--workspace', required=True)
    open_cmd.add_argument('--mode', default='agent')
    open_cmd.add_argument('--prompt', required=True)
    open_cmd.add_argument('--timeout', **shared_timeout)
    open_cmd.add_argument('--log')
    send_cmd = sub.add_parser('send')
    send_cmd.add_argument('--session', required=True)
    send_cmd.add_argument('--prompt', required=True)
    send_cmd.add_argument('--persona')
    send_cmd.add_argument('--model')
    send_cmd.add_argument('--workspace')
    send_cmd.add_argument('--mode')
    send_cmd.add_argument('--timeout', **shared_timeout)
    send_cmd.add_argument('--log')
    args = parser.parse_args(argv)
    runtime = CloudRuntime(timeout_s=args.timeout, log_path=args.log)
    try:
        if args.command == 'open':
            turn = runtime.open(args.persona, args.model, args.workspace, args.mode, args.prompt)
        else:
            turn = runtime.send(args.session, args.prompt, persona=args.persona, model=args.model,
                                workspace=args.workspace, mode=args.mode)
    except CloudRuntimeError as exc:
        print(f'rc={exc.returncode} {exc}', file=sys.stderr)
        return exc.returncode
    finally:
        runtime.close()
    _print_turn(turn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
