"""Testes do turno cloud com um `agent` falso no PATH.

O binário falso grava o argv e escreve a chave em `auth.json`, como o CLI real.
Nada aqui chama o `agent` de verdade nem lê `BRZA_CURSOR_API_KEY` do ambiente.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.cli.test_cloud_runtime
"""
import hashlib
import io
import json
import os
import signal
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'packages' / 'client' / 'bin'))
import cloud_runtime  # noqa: E402

SECRET = 'brza-test-key-not-real'
PROMPT = 'Responda apenas: P7-OK'
MODEL = 'grok-4.7-low'

FAKE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys, time
from pathlib import Path

behavior = os.environ.get("BRZA_FAKE_BEHAVIOR", "ok")
key = os.environ.get("CURSOR_API_KEY") or ""
if behavior == "hold":
    home = Path(os.environ["HOME"])
    cursor = home / ".config" / "cursor"
    cursor.mkdir(parents=True, exist_ok=True)
    (cursor / "auth.json").write_text(json.dumps({"apiKey": key}))
    time.sleep(30)

capture = Path(os.environ["BRZA_FAKE_CAPTURE"])
home = Path(os.environ["HOME"])
cursor = home / ".config" / "cursor"
cursor.mkdir(parents=True, exist_ok=True)
auth = cursor / "auth.json"
existed = auth.exists()
capture.write_text(json.dumps({
    "argv": sys.argv,
    "cursor_key_set": bool(key),
    "brza_absent": "BRZA_CURSOR_API_KEY" not in os.environ,
    "key_sha256": hashlib.sha256(key.encode()).hexdigest() if key else "",
    "auth_present_at_start": existed,
}, ensure_ascii=False))
auth.write_text(json.dumps({"apiKey": key}))
(cursor / "session-state").write_text("kept")
plugins = home / ".cursor" / "plugins"
plugins.mkdir(parents=True, exist_ok=True)
(plugins / "marker").write_text("cache")
leak = os.environ.get("BRZA_LEAK_WS")
if leak:
    path = Path(leak)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key)
if behavior == "timeout":
    time.sleep(30)
session = os.environ.get("BRZA_FAKE_SESSION", "sess-p7")
body = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 42,
    "result": "P7-OK",
    "session_id": session,
    "usage": {
        "inputTokens": 11,
        "outputTokens": 7,
        "cacheReadTokens": 3,
        "cacheWriteTokens": 2,
        "reasoningTokens": 1,
    },
}
raw_error = os.environ.get("BRZA_FAKE_IS_ERROR")
if raw_error is not None:
    body["is_error"] = json.loads(raw_error)
    body["result"] = "falhou"
number = os.environ.get("BRZA_FAKE_NUMBER")
if number == "integral":
    body["duration_ms"] = 42.0
    body["usage"]["inputTokens"] = 11.0
elif number == "fraction":
    body["duration_ms"] = 42.5
    body["usage"]["inputTokens"] = 11.5
elif behavior == "partial":
    body["usage"] = {"inputTokens": 0, "outputTokens": 1}
elif behavior == "no-usage":
    del body["usage"]
elif behavior == "badjson":
    print("nao-e-json")
    sys.exit(0)
print(json.dumps(body))
sys.exit(1 if behavior == "exit1" else 0)
'''


def files_with(root, secret):
    found = []
    path = Path(root) if root else None
    if path is None or not path.exists():
        return found
    targets = [path] if path.is_file() else []
    if path.is_dir():
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
            for name in filenames:
                targets.append(Path(dirpath) / name)
    for candidate in targets:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            blob = candidate.read_bytes()
        except OSError:
            continue
        if secret.encode() in blob:
            found.append(str(candidate))
    return found


class CloudRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='brza-cloud-test-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        bindir = self.tmp / 'bin'
        bindir.mkdir()
        agent = bindir / 'agent'
        agent.write_text(FAKE)
        agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
        (self.tmp / 'real-home').mkdir()
        (self.tmp / 'workspace').mkdir()
        self.log = self.tmp / 'measurements.jsonl'
        self.runtime = None

    def tearDown(self):
        if self.runtime is not None:
            self.runtime.close()

    def env(self, **extra):
        base = {
            'PATH': str(self.tmp / 'bin') + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': str(self.tmp / 'real-home'),
            'BRZA_FAKE_CAPTURE': str(self.tmp / 'capture.json'),
            'BRZA_FAKE_BEHAVIOR': 'ok',
            'BRZA_FAKE_SESSION': 'sess-p7',
            'BRZA_CURSOR_API_KEY': SECRET,
        }
        base.update(extra)
        base.pop('CURSOR_API_KEY', None)
        return base

    def runtime_for(self, **extra):
        timeout = extra.pop('timeout_s', 2)
        keep_home = extra.pop('keep_home', False)
        self.runtime = cloud_runtime.CloudRuntime(
            env=self.env(**extra), log_path=str(self.log), timeout_s=timeout, keep_home=keep_home)
        return self.runtime

    def spec(self, mode='agent', prompt=PROMPT, persona=None):
        return {
            'persona': persona or ('revisor' if mode == 'ask' else 'p7'),
            'model': MODEL,
            'workspace': str(self.tmp / 'workspace'),
            'mode': mode,
            'prompt': prompt,
        }

    def capture(self):
        return json.loads((self.tmp / 'capture.json').read_text())

    def flags(self):
        argv = self.capture()['argv']
        self.assertEqual(os.path.basename(argv[0]), 'agent')
        return argv[1:]

    def assert_secret_gone(self):
        roots = [self.tmp / 'real-home', self.log]
        if self.runtime is not None:
            roots.extend(self.runtime._homes.values())
        leaked = []
        for root in roots:
            leaked.extend(files_with(root, SECRET))
        capture = self.tmp / 'capture.json'
        if capture.exists() and SECRET.encode() in capture.read_bytes():
            leaked.append(str(capture))
        self.assertEqual(leaked, [])

    def test_open_returns_the_session_and_records_usage_with_absent_cost(self):
        runtime = self.runtime_for()
        turn = runtime.open(self.spec())
        self.assertEqual(turn.session_id, 'sess-p7')
        self.assertEqual(turn.result, 'P7-OK')
        self.assertEqual(self.flags(), [
            '-p', '--output-format', 'json', '--model', MODEL,
            '--workspace', str(self.tmp / 'workspace'), '--trust', '--', PROMPT,
        ])
        self.assertNotIn('--resume', self.flags())
        self.assertNotIn('--force', self.flags())
        self.assertNotIn('--api-key', self.flags())
        self.assertNotIn(SECRET, self.flags())
        line = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(line['event'], 'execution')
        self.assertEqual(line['runtime'], 'cloud')
        self.assertEqual(line['capability'], 'open')
        self.assertEqual(line['tokens'], {
            'input': 11, 'output': 7, 'cache_read': 3, 'cache_write': 2, 'reasoning': 1,
        })
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assertIsInstance(line['estimated_cost_usd'], str)
        got = self.capture()
        self.assertTrue(got['cursor_key_set'])
        self.assertTrue(got['brza_absent'])
        self.assertEqual(got['key_sha256'], hashlib.sha256(SECRET.encode()).hexdigest())
        self.assertNotIn('CURSOR_API_KEY', runtime.env)
        home = next(iter(runtime._homes.values()))
        self.assertEqual((Path(home) / '.config' / 'cursor' / 'session-state').read_text(), 'kept')
        plugins = Path(home) / '.cursor' / 'plugins'
        self.assertTrue(plugins.is_symlink())
        self.assertEqual((plugins / 'marker').read_text(), 'cache')
        self.assertEqual(os.stat(home).st_mode & 0o777, 0o700)
        self.assertTrue(str(home).endswith(os.path.join('.local', 'share', 'brza', 'cloud-runtime', 'p7')))
        self.assert_secret_gone()

    def test_files_with_reads_a_regular_file(self):
        self.log.write_text(SECRET)
        self.assertEqual(files_with(self.log, SECRET), [str(self.log)])

    def test_partial_usage_keeps_a_measured_zero_and_marks_the_rest_absent(self):
        self.runtime_for(BRZA_FAKE_BEHAVIOR='partial').open(self.spec())
        tokens = json.loads(self.log.read_text())['tokens']
        self.assertEqual(tokens['input'], 0)
        self.assertEqual(tokens['output'], 1)
        self.assertEqual(tokens['cache_read'], 'ausente')

    def test_integral_float_becomes_int_and_a_fraction_is_absent(self):
        runtime = self.runtime_for(BRZA_FAKE_NUMBER='integral')
        turn = runtime.open(self.spec())
        self.assertEqual(turn.duration_ms, 42)
        self.assertIsInstance(turn.duration_ms, int)
        self.assertEqual(turn.usage['input'], 11)
        self.assertIsInstance(turn.usage['input'], int)
        runtime = self.runtime_for(BRZA_FAKE_NUMBER='fraction')
        turn = runtime.open(self.spec())
        self.assertEqual(turn.duration_ms, 'ausente')
        self.assertEqual(turn.usage['input'], 'ausente')

    def test_is_error_counts_truthy_and_non_boolean_values(self):
        for raw in ('true', '1', '"sim"', '0'):
            with self.subTest(raw=raw):
                with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
                    self.runtime_for(BRZA_FAKE_IS_ERROR=raw).open(self.spec())
                self.assertIn('is_error', str(caught.exception))

    def test_boolean_false_is_not_an_error(self):
        turn = self.runtime_for(BRZA_FAKE_IS_ERROR='false').open(self.spec())
        self.assertFalse(turn.measurement['is_error'])

    def test_timeout_names_the_runtime_and_records_absent_tokens(self):
        started = time.monotonic()
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            self.runtime_for(BRZA_FAKE_BEHAVIOR='timeout', timeout_s=0.4).open(self.spec())
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn('cloud', str(caught.exception))
        line = json.loads(self.log.read_text())
        self.assertEqual(line['tokens']['input'], 'ausente')
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assert_secret_gone()

    def test_missing_key_is_not_declared_and_the_call_names_it(self):
        runtime = self.runtime_for(BRZA_CURSOR_API_KEY='')
        self.assertNotIn('open', runtime.capabilities())
        with self.assertRaises(cloud_runtime.MissingCapability) as caught:
            runtime.open(self.spec())
        message = str(caught.exception)
        self.assertIn('cloud', message)
        self.assertIn('open', message)
        self.assertIn('BRZA_CURSOR_API_KEY', message)
        self.assertFalse((self.tmp / 'capture.json').exists())

    def test_cursor_api_key_alone_declares_the_turn(self):
        env = self.env()
        env.pop('BRZA_CURSOR_API_KEY')
        env['CURSOR_API_KEY'] = SECRET
        runtime = cloud_runtime.CloudRuntime(env=env, log_path=str(self.log), timeout_s=2)
        self.runtime = runtime
        self.assertEqual(runtime.capabilities(), frozenset({'list', 'read', 'open', 'send'}))

    def test_missing_binary_is_not_declared_and_the_message_says_how_to_install(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        runtime = self.runtime_for(PATH=str(empty))
        self.assertEqual(runtime.capabilities(), frozenset({'list', 'read'}))
        with self.assertRaises(cloud_runtime.MissingCapability) as caught:
            runtime.send('sess-p7', 'de novo')
        message = str(caught.exception)
        self.assertIn('cloud', message)
        self.assertIn('send', message)
        self.assertIn('curl https://cursor.com/install', message)

    def test_ask_mode_is_a_flag_and_force_never_is(self):
        runtime = self.runtime_for()
        runtime.open(self.spec('ask'))
        flags = self.flags()
        self.assertEqual(flags[flags.index('--mode') + 1], 'ask')
        self.assertNotIn('--force', flags)
        self.assertNotIn('--resume', flags)
        runtime.send('sess-p7', 'de novo')
        flags = self.flags()
        self.assertEqual(flags[flags.index('--mode') + 1], 'ask')
        self.assertEqual(flags[flags.index('--resume') + 1], 'sess-p7')

    def test_a_prompt_that_starts_with_a_dash_stays_after_the_separator(self):
        self.runtime_for().open(self.spec(prompt='--probe-dash'))
        flags = self.flags()
        self.assertEqual(flags[flags.index('--') + 1], '--probe-dash')

    def test_send_resumes_the_same_session(self):
        runtime = self.runtime_for()
        opened = runtime.open(self.spec())
        home_after_open = runtime._homes['p7']
        sent = runtime.send(opened.session_id, 'Qual foi a sua resposta anterior?')
        self.assertEqual(sent.session_id, opened.session_id)
        flags = self.flags()
        self.assertEqual(flags[flags.index('--resume') + 1], 'sess-p7')
        self.assertEqual(flags[flags.index('--workspace') + 1], str(self.tmp / 'workspace'))
        self.assertEqual(flags[flags.index('--model') + 1], MODEL)
        self.assertEqual(runtime._homes['p7'], home_after_open)
        self.assert_secret_gone()

    def test_send_without_metadata_does_not_spawn(self):
        runtime = self.runtime_for()
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            runtime.send('sess-p7', 'de novo')
        self.assertIn('cloud', str(caught.exception))
        self.assertFalse((self.tmp / 'capture.json').exists())

    def test_cold_ask_send_keeps_the_mode_from_the_persona_file(self):
        first = self.runtime_for(keep_home=True)
        opened = first.open(self.spec('ask'))
        home = first._homes['revisor']
        meta = json.loads((Path(home) / 'persona.json').read_text())
        self.assertEqual(meta['mode'], 'ask')
        self.assertNotIn(SECRET, json.dumps(meta))
        first.close()
        self.runtime = None
        self.assertTrue(Path(home).is_dir())
        second = cloud_runtime.CloudRuntime(env=self.env(), log_path=str(self.log), timeout_s=2, keep_home=True)
        self.runtime = second
        second.send(opened.session_id, 'de novo')
        flags = self.flags()
        self.assertEqual(flags[flags.index('--mode') + 1], 'ask')
        self.assertEqual(flags[flags.index('--model') + 1], MODEL)
        self.assertEqual(flags[flags.index('--resume') + 1], 'sess-p7')
        self.assertEqual(second._homes['revisor'], home)

    def test_an_explicit_mode_overrides_the_stored_one(self):
        runtime = self.runtime_for(keep_home=True)
        opened = runtime.open(self.spec('ask'))
        runtime.override_meta(opened.session_id, mode='plan')
        runtime.send(opened.session_id, 'de novo')
        self.assertEqual(self.flags()[self.flags().index('--mode') + 1], 'plan')

    def test_close_deletes_the_persona_home(self):
        runtime = self.runtime_for()
        runtime.open(self.spec())
        home = runtime._homes['p7']
        planted = Path(home) / 'planted.txt'
        planted.write_text(SECRET)
        runtime.close()
        self.runtime = None
        self.assertFalse(Path(home).exists())

    def test_a_crash_leftover_is_gone_before_the_spawn(self):
        runtime = self.runtime_for()
        home = runtime._home_for('p7')
        auth = Path(home) / '.config' / 'cursor' / 'auth.json'
        auth.parent.mkdir(parents=True)
        auth.write_text(SECRET)
        runtime.open(self.spec())
        self.assertFalse(self.capture()['auth_present_at_start'])
        self.assert_secret_gone()

    def test_parent_auth_stays_and_a_workspace_leak_goes_away(self):
        parent = self.tmp / 'real-home' / '.config' / 'cursor' / 'auth.json'
        parent.parent.mkdir(parents=True)
        parent.write_bytes(b'login-' + SECRET.encode())
        before = parent.read_bytes()
        old = self.tmp / 'workspace' / 'old.txt'
        old.write_text(SECRET)
        leak = self.tmp / 'workspace' / 'leak.txt'
        runtime = self.runtime_for(BRZA_LEAK_WS=str(leak))
        runtime.open(self.spec())
        self.assertEqual(parent.read_bytes(), before)
        self.assertFalse(leak.exists())
        self.assertEqual(old.read_text(), SECRET)

    def test_a_symlink_in_the_home_path_is_refused(self):
        base = self.tmp / 'real-home' / '.local' / 'share' / 'brza'
        base.mkdir(parents=True)
        (self.tmp / 'elsewhere').mkdir()
        (base / 'cloud-runtime').symlink_to(self.tmp / 'elsewhere')
        runtime = self.runtime_for()
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            runtime.open(self.spec())
        self.assertIn('symlink', str(caught.exception))
        self.assertFalse((self.tmp / 'capture.json').exists())

    def test_a_key_symlink_outside_the_parent_home_loses_the_target(self):
        runtime = self.runtime_for()
        home = runtime._home_for('p7')
        target = self.tmp / 'outside-key'
        target.write_text(SECRET)
        auth = Path(home) / '.config' / 'cursor' / 'auth.json'
        auth.parent.mkdir(parents=True)
        auth.symlink_to(target)
        runtime.open(self.spec())
        self.assertFalse(target.exists())
        self.assert_secret_gone()

    def test_a_key_symlink_into_the_parent_home_is_refused_and_the_target_stays(self):
        parent = self.tmp / 'real-home' / '.config' / 'cursor' / 'auth.json'
        parent.parent.mkdir(parents=True)
        parent.write_text('login-' + SECRET)
        before = parent.read_bytes()
        runtime = self.runtime_for()
        home = runtime._home_for('p7')
        auth = Path(home) / '.config' / 'cursor' / 'auth.json'
        auth.parent.mkdir(parents=True)
        auth.symlink_to(parent)
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            runtime.open(self.spec())
        self.assertIn('HOME pai', str(caught.exception))
        self.assertEqual(parent.read_bytes(), before)
        self.assertFalse((self.tmp / 'capture.json').exists())

    def test_a_fifo_does_not_block_the_scrub(self):
        runtime = self.runtime_for()
        home = runtime._home_for('p7')
        fifo = Path(home) / 'pipe'
        os.mkfifo(fifo)

        def alarm(_signum, _frame):
            raise TimeoutError('fifo')

        previous = signal.signal(signal.SIGALRM, alarm)
        signal.alarm(2)
        try:
            cloud_runtime._scrub_tree(home, SECRET, runtime, None, False, None)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        self.assertTrue(fifo.exists())

    def test_cli_send_requires_session_and_prompt(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cloud_runtime.main(['send'])
        self.assertEqual(caught.exception.code, 2)

    def test_a_failed_communicate_kills_the_agent_after_the_key_file_exists(self):
        from unittest import mock
        pids = []
        real_popen = cloud_runtime.subprocess.Popen
        runtime = self.runtime_for(BRZA_FAKE_BEHAVIOR='hold', timeout_s=2)

        def popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            pids.append(proc.pid)

            def boom(timeout=None):
                deadline = time.time() + 2
                auth = None
                while time.time() < deadline:
                    homes = list(runtime._homes.values())
                    if homes:
                        auth = Path(homes[0]) / '.config' / 'cursor' / 'auth.json'
                        if auth.is_file() and SECRET.encode() in auth.read_bytes():
                            break
                    time.sleep(0.02)
                else:
                    raise AssertionError('auth.json com a chave não apareceu antes do kill')
                raise RuntimeError('communicate falhou')

            proc.communicate = boom
            return proc

        def kill_leftovers():
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass

        self.addCleanup(kill_leftovers)
        with mock.patch.object(cloud_runtime.subprocess, 'Popen', popen):
            with self.assertRaises(RuntimeError):
                runtime.open(self.spec())
        self.assertTrue(pids)
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)
        self.assert_secret_gone()


if __name__ == '__main__':
    unittest.main()
