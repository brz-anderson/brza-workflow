"""Testes do turno cloud com um `agent` falso no PATH.

O binário falso grava o argv e escreve a chave em `auth.json`, como o CLI real.
Nada aqui chama o `agent` de verdade nem lê `BRZA_CURSOR_API_KEY` do ambiente.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.cli.test_cloud_runtime
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'packages' / 'client'))
import cloud_runtime  # noqa: E402

SECRET = 'brza-test-key-not-real'
PROMPT = 'Responda apenas: P7-OK'
MODEL = 'grok-4.7-low'

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path

key = os.environ.get("CURSOR_API_KEY") or ""
brza = os.environ.get("BRZA_CURSOR_API_KEY") or ""
capture = Path(os.environ["BRZA_FAKE_CAPTURE"])
capture.write_text(json.dumps({
    "argv": sys.argv,
    "key_matches": bool(key) and key == brza,
    "cursor_key_set": bool(key),
}, ensure_ascii=False))

home = Path(os.environ["HOME"])
cursor = home / ".config" / "cursor"
cursor.mkdir(parents=True, exist_ok=True)
(cursor / "auth.json").write_text(json.dumps({"apiKey": key}))
(cursor / "session-state").write_text("kept")

leak = os.environ.get("BRZA_LEAK_AUTH")
if leak:
    path = Path(leak)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key)

behavior = os.environ.get("BRZA_FAKE_BEHAVIOR", "ok")
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
if behavior == "error":
    body["is_error"] = True
    body["subtype"] = "error"
    body["result"] = "falhou"
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
    if not root or not Path(root).exists():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            if secret.encode() in blob:
                found.append(str(path))
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
            'BRZA_LEAK_AUTH': str(self.tmp / 'real-home' / '.config' / 'cursor' / 'auth.json'),
        }
        base.update(extra)
        base.pop('CURSOR_API_KEY', None)
        return base

    def runtime_for(self, **extra):
        timeout = extra.pop('timeout_s', 2)
        self.runtime = cloud_runtime.CloudRuntime(
            env=self.env(**extra), log_path=str(self.log), timeout_s=timeout)
        return self.runtime

    def capture(self):
        return json.loads((self.tmp / 'capture.json').read_text())

    def flags(self):
        argv = self.capture()['argv']
        self.assertEqual(os.path.basename(argv[0]), 'agent')
        return argv[1:]

    def open(self, mode='agent', prompt=PROMPT):
        return self.runtime_for().open('revisor' if mode == 'ask' else 'p7', MODEL,
                                       str(self.tmp / 'workspace'), mode, prompt)

    def assert_secret_gone(self):
        roots = [self.tmp / 'real-home', self.tmp / 'measurements.jsonl']
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
        turn = self.open()
        self.assertEqual(turn.session_id, 'sess-p7')
        self.assertEqual(turn.result, 'P7-OK')
        self.assertEqual(self.flags(), [
            '-p', '--output-format', 'json', '--model', MODEL,
            '--workspace', str(self.tmp / 'workspace'), '--trust', PROMPT,
        ])
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
        self.assertNotEqual(line['estimated_cost_usd'], 0)
        self.assertTrue(self.capture()['key_matches'])
        self.assertNotIn('CURSOR_API_KEY', self.runtime._env)
        self.assertNotIn('CURSOR_API_KEY', os.environ)
        state = Path(next(iter(self.runtime._homes.values()))) / '.config' / 'cursor' / 'session-state'
        self.assertEqual(state.read_text(), 'kept')
        self.assert_secret_gone()

    def test_partial_usage_keeps_a_measured_zero_and_marks_the_rest_absent(self):
        self.runtime_for(BRZA_FAKE_BEHAVIOR='partial').open('p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
        tokens = json.loads(self.log.read_text())['tokens']
        self.assertEqual(tokens['input'], 0)
        self.assertEqual(tokens['output'], 1)
        self.assertEqual(tokens['cache_read'], 'ausente')
        self.assertEqual(tokens['cache_write'], 'ausente')
        self.assertEqual(tokens['reasoning'], 'ausente')
        self.assertEqual(json.loads(self.log.read_text())['estimated_cost_usd'], 'ausente')

    def test_is_error_fails_and_still_records_usage(self):
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            self.runtime_for(BRZA_FAKE_BEHAVIOR='error').open(
                'p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
        self.assertIn('is_error', str(caught.exception))
        self.assertIn('cloud', str(caught.exception))
        line = json.loads(self.log.read_text())
        self.assertEqual(line['tokens']['input'], 11)
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assertTrue(line['is_error'])

    def test_timeout_names_the_runtime_and_records_absent_tokens(self):
        started = __import__('time').monotonic()
        with self.assertRaises(cloud_runtime.CloudTurnError) as caught:
            self.runtime_for(BRZA_FAKE_BEHAVIOR='timeout', timeout_s=0.4).open(
                'p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
        self.assertLess(__import__('time').monotonic() - started, 5)
        self.assertIn('timeout', str(caught.exception))
        self.assertIn('cloud', str(caught.exception))
        line = json.loads(self.log.read_text())
        self.assertEqual(line['tokens']['input'], 'ausente')
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assert_secret_gone()

    def test_missing_key_is_not_declared_and_the_call_names_it(self):
        runtime = self.runtime_for(BRZA_CURSOR_API_KEY='')
        self.runtime = runtime
        self.assertNotIn('open', runtime.capabilities())
        self.assertNotIn('send', runtime.capabilities())
        with self.assertRaises(cloud_runtime.CapabilityError) as caught:
            runtime.open('p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
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
        self.assertEqual(runtime.capabilities(), frozenset({'open', 'send'}))

    def test_missing_binary_is_not_declared_and_the_message_says_how_to_install(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        runtime = self.runtime_for(PATH=str(empty))
        self.assertEqual(runtime.capabilities(), frozenset())
        with self.assertRaises(cloud_runtime.CapabilityError) as caught:
            runtime.send('sess-p7', 'de novo')
        message = str(caught.exception)
        self.assertIn('cloud', message)
        self.assertIn('send', message)
        self.assertIn('agent', message)
        self.assertIn('curl https://cursor.com/install', message)

    def test_ask_mode_is_a_flag_and_force_never_is(self):
        runtime = self.runtime_for()
        runtime.open('revisor', MODEL, str(self.tmp / 'workspace'), 'ask', PROMPT)
        flags = self.flags()
        self.assertEqual(flags[flags.index('--mode') + 1], 'ask')
        self.assertNotIn('--force', flags)
        self.assertNotIn('--yolo', flags)
        self.assertNotIn('--api-key', flags)
        self.assertNotIn('--resume', flags)
        runtime.send('sess-p7', 'de novo')
        flags = self.flags()
        self.assertEqual(flags[flags.index('--mode') + 1], 'ask')
        self.assertEqual(flags[flags.index('--resume') + 1], 'sess-p7')
        self.assertNotIn('--force', flags)

    def test_send_resumes_the_same_session(self):
        runtime = self.runtime_for()
        opened = runtime.open('p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
        home_after_open = next(iter(runtime._homes.values()))
        sent = runtime.send(opened.session_id, 'Qual foi a sua resposta anterior?')
        self.assertEqual(sent.session_id, opened.session_id)
        flags = self.flags()
        self.assertEqual(flags[flags.index('--resume') + 1], 'sess-p7')
        self.assertEqual(flags[flags.index('--workspace') + 1], str(self.tmp / 'workspace'))
        self.assertEqual(flags[flags.index('--model') + 1], MODEL)
        self.assertNotIn('--force', flags)
        self.assertNotIn(SECRET, flags)
        self.assertEqual(list(runtime._homes), ['p7'])
        self.assertEqual(runtime._homes['p7'], home_after_open)
        line = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(line['capability'], 'send')
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assert_secret_gone()

    def test_undeclared_list_fails_even_when_the_turn_is_available(self):
        runtime = self.runtime_for()
        self.assertEqual(runtime.capabilities(), frozenset({'open', 'send'}))
        with self.assertRaises(cloud_runtime.CapabilityError) as caught:
            runtime.list()
        self.assertIn('list', str(caught.exception))
        self.assertIn('cloud', str(caught.exception))
        with self.assertRaises(cloud_runtime.CapabilityError) as read_caught:
            runtime.read('sess-p7')
        self.assertIn('read', str(read_caught.exception))

    def test_bad_json_fails_without_writing_the_key(self):
        with self.assertRaises(cloud_runtime.CloudTurnError):
            self.runtime_for(BRZA_FAKE_BEHAVIOR='badjson').open(
                'p7', MODEL, str(self.tmp / 'workspace'), 'agent', PROMPT)
        line = json.loads(self.log.read_text())
        self.assertEqual(line['estimated_cost_usd'], 'ausente')
        self.assertEqual(line['tokens']['output'], 'ausente')
        self.assert_secret_gone()


if __name__ == '__main__':
    unittest.main()
