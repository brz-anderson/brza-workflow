"""Testes do brza_runtime: o envio ao chat, o git e o worktree do brza-studio.

    python3 -m unittest discover -s tests/cli
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
CLIENT = ROOT / 'packages' / 'client' / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402


def git(*args, cwd: Path):
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class SendTest(unittest.TestCase):
    def test_canonical_json_sorts_keys_and_keeps_accents(self):
        self.assertEqual(runtime.canonicalize({'b': [1, 'ã', {'d': 2, 'c': True}], 'a': None}),
                         '{"a":null,"b":[1,"ã",{"c":true,"d":2}]}')

    def test_send_mutation_matches_the_app_fingerprint(self):
        # a mesma impressão digital do teste do brza-run, conferida no node com o brza-studio
        text = 'Rodei pelo ▶:\n```bash\nls\t-la\n```\n\x01 ção 😀 \u2028 "aspas" \\ fim'
        params = runtime.send_mutation('claude_x', text, 1, 1792900000000, '0' * 32)
        self.assertEqual(params['envelope']['payloadFingerprint'],
                         '6c1f3d3de96998c2cb1c2ed99684ed68b1cdea5a8bc9bff9955819b4535bfb31')
        self.assertEqual(params['body']['blocks'], [{'type': 'text', 'text': text}])

    def test_send_to_chat_queues_after_the_last_fence(self):
        calls = []

        def rpc(method, params=None):
            calls.append((method, params))
            return {'page': {'fence': 7}} if method == 'agentSession.history' else {}

        with patch.object(runtime, 'rpc', rpc):
            runtime.send_to_chat('claude_1', 'faça a tarefa')
        self.assertEqual([m for m, _ in calls], ['agentSession.history', 'agentSession.send'])
        params = calls[1][1]
        payload = runtime.canonicalize({'method': 'agentSession.send', 'sessionId': 'claude_1',
                                        'fields': {'body': params['body'], 'delivery': 'queue-if-active'}})
        self.assertEqual(params['envelope']['expectedRuntimeFence'], 7)
        self.assertEqual(params['envelope']['payloadFingerprint'], hashlib.sha256(payload.encode()).hexdigest())
        self.assertRegex(params['envelope']['clientOperationId'], r'^\d{13}-[0-9a-f]{32}$')


class RpcCallerChatTest(unittest.TestCase):
    """O `agentSession.send` diz ao app de que chat vem a mensagem; é o que a faz chegar como tarefa, não como a da pessoa."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir='/tmp'))  # o caminho do socket unix tem limite de tamanho
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(self.dir)]))
        endpoint = self.dir / 's.sock'
        (self.dir / 'orca-runtime.json').write_text(json.dumps(
            {'authToken': 't', 'transports': [{'kind': 'unix', 'endpoint': str(endpoint)}]}))
        self.requests = []
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(endpoint))
        server.listen()
        self.addCleanup(server.close)

        def serve():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    self.requests.append(json.loads(conn.makefile().readline()))
                    conn.sendall(b'{"ok": true, "result": {}}\n')

        threading.Thread(target=serve, daemon=True).start()

    def call(self, method, chat):
        env = {k: v for k, v in os.environ.items() if k != 'ORCA_AGENT_SESSION_ID'}
        env['ORCA_USER_DATA_PATH'] = str(self.dir)
        if chat:
            env['ORCA_AGENT_SESSION_ID'] = chat
        with patch.dict(os.environ, env, clear=True):
            runtime.rpc(method, {})
        return self.requests[-1]

    def test_send_names_the_chat_of_the_agent_that_sends(self):
        request = self.call('agentSession.send', 'claude_abc')
        self.assertEqual(request['orchestrationCompatibilityEvidence'], {'agentSessionId': 'claude_abc'})

    def test_send_outside_a_chat_names_none(self):
        self.assertNotIn('orchestrationCompatibilityEvidence', self.call('agentSession.send', None))

    def test_a_read_names_no_chat(self):
        self.assertNotIn('orchestrationCompatibilityEvidence', self.call('agentSession.history', 'claude_abc'))


AGENT_REVIEWER = """---
name: reviewer
description: Revisa o diff em busca de bugs
model: opus
tools: [Read, Grep, Glob]
memory: project
effort: high
---

Você revisa.

Segunda linha, com acento: ação.
"""

AGENT_COLLECTOR = """---
description: "Coleta fatos: com dois pontos"
model: haiku
tools:
  - Read
  - Bash
maxTurns: 12
---
Você coleta.
"""


class AgentsJsonTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ws = Path(temp.name)
        self.folder = self.ws / '.claude' / 'agents'

    def agent(self, name: str, text: str):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / name).write_text(text)

    def test_without_the_directory_or_without_agents_it_is_none(self):
        self.assertIsNone(runtime.agents_json(self.ws))
        self.folder.mkdir(parents=True)
        self.assertIsNone(runtime.agents_json(self.ws))
        self.agent('README.md', '# sem frontmatter\n')
        self.assertIsNone(runtime.agents_json(self.ws))

    def test_each_file_becomes_an_agent_in_name_order_with_the_body_as_prompt(self):
        self.agent('reviewer.md', AGENT_REVIEWER)
        self.agent('collector.md', AGENT_COLLECTOR)
        text = runtime.agents_json(self.ws)
        agents = json.loads(text)
        self.assertEqual(list(agents), ['collector', 'reviewer'])
        self.assertEqual(agents['reviewer'], {
            'description': 'Revisa o diff em busca de bugs', 'prompt': 'Você revisa.\n\nSegunda linha, com acento: ação.',
            'model': 'opus', 'tools': ['Read', 'Grep', 'Glob'], 'memory': 'project', 'effort': 'high'})
        self.assertEqual(agents['collector'], {
            'description': 'Coleta fatos: com dois pontos', 'prompt': 'Você coleta.', 'model': 'haiku',
            'tools': ['Read', 'Bash'], 'maxTurns': 12})
        self.assertEqual(list(agents['reviewer']), ['description', 'prompt', 'model', 'tools', 'memory', 'effort'])
        self.assertIn('ação', text)
        self.assertEqual(text, runtime.agents_json(self.ws))

    def test_optional_keys_stay_out_when_the_frontmatter_lacks_them(self):
        self.agent('designer.md', '---\ndescription: Desenha\nmodel: opus\n---\nCorpo\n')
        self.assertEqual(json.loads(runtime.agents_json(self.ws)),
                         {'designer': {'description': 'Desenha', 'prompt': 'Corpo', 'model': 'opus'}})

    def test_tools_as_a_comma_separated_scalar_is_a_list_and_the_name_falls_back_to_the_file(self):
        self.agent('implementer.md', '---\ndescription: x\nmodel: sonnet\ntools: Read, Edit, Write\n---\ny\n')
        self.agent('um.md', '---\ndescription: x\ntools: Read\n---\ny\n')
        agents = json.loads(runtime.agents_json(self.ws))
        self.assertEqual(agents['implementer']['tools'], ['Read', 'Edit', 'Write'])
        self.assertEqual(agents['um']['tools'], ['Read'])

    def test_a_block_description_is_joined(self):
        self.agent('a.md', '---\ndescription: >\n  uma frase\n  em duas linhas\nmodel: haiku\n---\nz\n')
        self.assertEqual(json.loads(runtime.agents_json(self.ws))['a']['description'], 'uma frase em duas linhas')

    def test_frontmatter_without_the_closing_line_is_ignored(self):
        self.agent('quebrado.md', '---\ndescription: x\n')
        self.assertIsNone(runtime.agents_json(self.ws))

    def test_the_default_workspace_is_the_runtime_one(self):
        self.agent('a.md', '---\ndescription: x\n---\ny\n')
        with patch.object(runtime, 'WORKSPACE', self.ws):
            self.assertIn('"a"', runtime.agents_json())


class AutoMemoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = self.root / '.claude' / 'settings.local.json'

    def test_it_creates_the_file_and_the_folder(self):
        self.assertTrue(runtime.set_auto_memory(self.root, Path('/w/memory')))
        self.assertEqual(json.loads(self.settings.read_text()), {'autoMemoryDirectory': '/w/memory'})
        self.assertFalse(runtime.set_auto_memory(self.root, Path('/w/memory')))

    def test_it_merges_and_keeps_the_other_keys(self):
        self.settings.parent.mkdir()
        self.settings.write_text('{"env": {"A": "1"}, "autoMemoryDirectory": "/velho"}')
        self.assertTrue(runtime.set_auto_memory(self.root, Path('/w/memory')))
        self.assertEqual(json.loads(self.settings.read_text()),
                         {'env': {'A': '1'}, 'autoMemoryDirectory': '/w/memory'})

    def test_unreadable_json_or_a_non_object_raises_and_is_not_overwritten(self):
        self.settings.parent.mkdir()
        for text in ('{x', '[1]'):
            self.settings.write_text(text)
            with self.assertRaises(runtime.BrzaError):
                runtime.set_auto_memory(self.root, Path('/w/memory'))
            self.assertEqual(self.settings.read_text(), text)


class StudioTest(unittest.TestCase):
    def test_studio_comes_from_the_environment_or_the_projects_folder(self):
        self.assertEqual(runtime.STUDIO, Path(os.environ.get('BRZA_STUDIO')
                                              or Path.home() / 'Projects' / 'brza-studio'))


class DiscoverTest(unittest.TestCase):
    """O workspace do cwd, sem legado de nome fixo (A5), e a BRZA_WORKSPACE que o cwd de outro repositório não toma (A1)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'
        legacy = self.home / 'Projects' / 'brza-workspace'  # onde o legado do X10 cairia
        legacy.mkdir(parents=True)
        env = {'HOME': str(self.home), 'BRZA_REGISTRY': str(self.base / 'nenhum.toml')}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for name in ('BRZA_WORKSPACE', 'BRZA_STUDIO'):
            os.environ.pop(name, None)
        self.ws = self.workspace('ws')

    def workspace(self, name: str) -> Path:
        root = self.base / name
        root.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(root)], check=True)
        (root / '.brza').mkdir()
        (root / '.brza' / 'workspace.toml').write_text('schema = 1\n[workspace]\nname = "x"\n')
        git('add', '.', cwd=root)
        git('commit', '-q', '-m', 'um', cwd=root)
        return root

    def test_without_a_workspace_the_root_is_none_and_the_fixed_name_is_not_taken(self):  # A5
        plain = self.base / 'plain'
        plain.mkdir()
        root, _, manifest = runtime.discover(plain)
        self.assertIsNone(root)
        self.assertEqual(manifest, {})
        os.environ['BRZA_WORKSPACE'] = str(self.home / 'Projects' / 'brza-workspace')  # sem manifesto
        self.assertIsNone(runtime.discover(plain)[0])

    def test_the_workspace_of_the_cwd_is_found_by_its_manifest(self):
        root, _, manifest = runtime.discover(self.ws)
        self.assertEqual(root, self.ws)
        self.assertEqual(manifest['workspace']['name'], 'x')

    def test_require_workspace_refuses_with_a_clear_message_that_is_refused_and_brza_error(self):  # A5
        with patch.object(runtime, 'WORKSPACE', None):
            with self.assertRaisesRegex(runtime.NoWorkspace, 'nenhum workspace achado'):
                runtime.require_workspace()
            self.assertTrue(issubclass(runtime.NoWorkspace, (runtime.Refused, runtime.BrzaError)))
            with self.assertRaises(runtime.NoWorkspace):
                runtime.workstream_root('x')
            with self.assertRaises(runtime.NoWorkspace):
                runtime.agents_json()
        with patch.object(runtime, 'WORKSPACE', self.ws):
            self.assertEqual(runtime.require_workspace(), self.ws)

    def test_foreign_cwd_names_both_paths_when_the_cwd_is_another_repository(self):  # A1
        other = self.workspace('ws2')
        os.environ['BRZA_WORKSPACE'] = str(self.ws)
        message = runtime.foreign_cwd(other)
        self.assertIn(str(other), message)
        self.assertIn(str(self.ws), message)
        for cwd in (self.ws, self.base, self.home):  # o workspace, e fora de qualquer repositório
            self.assertIsNone(runtime.foreign_cwd(cwd), cwd)
        del os.environ['BRZA_WORKSPACE']
        self.assertIsNone(runtime.foreign_cwd(other))  # sem a variável, o cwd é o workspace


class WorktreeTest(unittest.TestCase):
    """Um brza-studio de mentira: um clone de uma origin local, na master."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        origin, seed, self.studio = self.base / 'origin.git', self.base / 'seed', self.base / 'studio'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(origin), str(seed)], check=True, capture_output=True)
        git('commit', '-q', '--allow-empty', '-m', 'um', cwd=seed)
        git('push', '-q', 'origin', 'HEAD:master', cwd=seed)
        subprocess.run(['git', 'clone', '-q', str(origin), str(self.studio)], check=True)
        # um commit que só chega ao studio pelo fetch
        git('commit', '-q', '--allow-empty', '-m', 'dois', cwd=seed)
        git('push', '-q', 'origin', 'HEAD:master', cwd=seed)
        self.tip = git('rev-parse', 'HEAD', cwd=seed)
        p = patch.object(runtime, 'STUDIO', self.studio)
        p.start()
        self.addCleanup(p.stop)

    def test_git_returns_stdout_and_turns_failure_into_brza_error(self):
        self.assertEqual(runtime.git('rev-parse', '--abbrev-ref', 'HEAD').strip(), 'master')
        with self.assertRaisesRegex(runtime.BrzaError, 'nao-existe'):
            runtime.git('rev-parse', 'nao-existe')

    def test_worktree_add_fetches_and_branches_from_origin_master(self):
        path = runtime.worktree_add(self.base / 'studio' / '.worktrees' / 'x', branch='feat/x')
        self.assertEqual(git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path), 'feat/x')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=path), self.tip)

    def test_worktree_add_without_branch_is_detached(self):
        path = runtime.worktree_add(self.base / 'tmp-wt')
        self.assertEqual(git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path), 'HEAD')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=path), self.tip)

    def test_worktree_add_without_fetch_uses_what_is_known(self):
        path = runtime.worktree_add(self.base / 'tmp-wt', fetch=False)
        self.assertNotEqual(git('rev-parse', 'HEAD', cwd=path), self.tip)

    def test_worktree_remove_takes_the_clean_worktree_and_refuses_the_dirty(self):
        clean, dirty = runtime.worktree_add(self.base / 'limpo'), runtime.worktree_add(self.base / 'sujo')
        runtime.worktree_remove(clean)
        self.assertFalse(clean.exists())
        (dirty / 'novo.txt').write_text('x')
        with self.assertRaises(runtime.BrzaError):
            runtime.worktree_remove(dirty)
        self.assertTrue(dirty.exists())


class InstallOfflineTest(unittest.TestCase):
    """O pnpm trocado por um falso, que sai com o código gravado em `code` e anota os argumentos."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        pnpm = bin_dir / 'pnpm'
        pnpm.write_text(f'#!/bin/sh\necho "$(pwd -P) $*" >> "{self.base}/calls"\necho falhou >&2\n'
                        f'exit $(cat "{self.base}/code")\n')
        pnpm.chmod(0o755)
        p = patch.dict(os.environ, {'PATH': f'{bin_dir}:/usr/bin:/bin'})
        p.start()
        self.addCleanup(p.stop)
        self.tree = self.base / 'tree'
        self.tree.mkdir()

    def test_failure_names_the_online_command_and_success_passes(self):
        (self.base / 'code').write_text('1')
        with self.assertRaisesRegex(runtime.BrzaError, 'pnpm install --frozen-lockfile'):
            runtime.install_offline(self.tree)
        (self.base / 'code').write_text('0')
        runtime.install_offline(self.tree)
        calls = (self.base / 'calls').read_text().splitlines()
        self.assertEqual(calls, [f'{self.tree.resolve()} install --offline --frozen-lockfile'] * 2)


if __name__ == '__main__':
    unittest.main()
