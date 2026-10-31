"""Testes do brza-install (D10 do PLN-006): os links apontam para o checkout do brza-workflow, e nunca para um workspace.
Nada aqui lê o ~/.local/bin nem o ~/.config reais: o LOCAL_BIN é um diretório temporário, e o teste de processo roda
com o HOME trocado.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
INSTALL = runpy.run_path(str(BIN / 'brza-install'))
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402

# O cabeçalho que um script local (brza-app, brza-tree…) usa para achar o brza_runtime pelo checkout do brza-workflow
# (D10): a BRZA_WORKFLOW, ou o realpath do brza-note ligado em ~/.local/bin. Está também no docstring do brza-install.
LOCAL_HEADER = '''\
import os, shutil, sys
from pathlib import Path
_flow = os.environ.get('BRZA_WORKFLOW')
_bin = Path(_flow) / 'bin' if _flow else Path(os.path.realpath(shutil.which('brza-note') or '')).parent
sys.path.insert(0, str(_bin))
import brza_runtime as runtime
'''


def make_exec(path: Path, text: str = '#!/bin/sh\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class InstallCase(unittest.TestCase):
    """Um checkout do brza-workflow, um workspace com scripts/brza/ e um ~/.local/bin, todos temporários."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.bin = self.base / 'local-bin'
        self.workflow = self.base / 'brza-workflow'
        self.core, self.client = self.workflow / 'bin', self.workflow / 'packages' / 'client' / 'bin'
        for name in ('brza-install', 'brza-note', 'brza-where'):
            make_exec(self.core / name)
        (self.core / 'brza_runtime.py').write_text('')
        (self.core / 'brza-rascunho').write_text('não executável\n')
        for name in ('brza-doctor', 'brza-run', 'claude-brza'):
            make_exec(self.client / name)
        self.workspace = self.base / 'ws'
        self.local = self.workspace / 'scripts' / 'brza'
        for name in ('brza-app', 'brza-tree'):
            make_exec(self.local / name)
        for p in (patch.object(runtime, 'LOCAL_BIN', self.bin),
                  patch.dict(os.environ, {'BRZA_WORKFLOW': str(self.workflow)})):
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *argv):
        """(código, stdout, stderr) do main; o sys.exit com texto vira código 1 e a mensagem em stderr."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                INSTALL['main'](list(argv))
            except SystemExit as e:
                if isinstance(e.code, str):
                    code = 1
                    print(e.code, file=err)
                else:
                    code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def linked(self) -> list[str]:
        return sorted(p.name for p in self.bin.iterdir()) if self.bin.exists() else []

    def target(self, name: str) -> Path:
        return Path(os.readlink(self.bin / name))

    def alien(self, name: str, to: str = '/tmp/outro'):
        self.bin.mkdir(parents=True, exist_ok=True)
        (self.bin / name).symlink_to(to)


class LinksTest(InstallCase):
    def test_all_links_the_core_commands_to_the_checkout_and_nothing_else(self):
        code, out, _ = self.run_main('--all')
        self.assertEqual(code, 0)
        self.assertEqual(self.linked(), ['brza-install', 'brza-note', 'brza-where'])
        for name in self.linked():
            self.assertEqual(self.target(name), self.core / name)
        self.assertIn('brza-note', out)

    def test_client_links_the_client_commands_but_not_the_claude_brza(self):
        code, _, _ = self.run_main('--client')
        self.assertEqual(code, 0)
        self.assertEqual(self.linked(), ['brza-doctor', 'brza-run'])
        self.assertEqual(self.target('brza-run'), self.client / 'brza-run')

    def test_all_and_client_together(self):
        self.assertEqual(self.run_main('--all', '--client')[0], 0)
        self.assertEqual(self.linked(), ['brza-doctor', 'brza-install', 'brza-note', 'brza-run', 'brza-where'])

    def test_local_takes_the_scripts_brza_of_the_workspace_given(self):
        code, _, _ = self.run_main('--local', str(self.workspace))
        self.assertEqual(code, 0)
        self.assertEqual(self.linked(), ['brza-app', 'brza-tree'])
        self.assertEqual(self.target('brza-app'), self.local / 'brza-app')

    def test_local_takes_the_folder_itself_when_it_has_no_scripts_brza(self):
        self.assertEqual(self.run_main('--local', str(self.local))[0], 0)
        self.assertEqual(self.linked(), ['brza-app', 'brza-tree'])

    def test_local_with_a_missing_folder_or_no_command_is_refused(self):
        empty = self.base / 'vazio'
        empty.mkdir()
        for given in (self.base / 'nao-existe', empty):
            with self.subTest(given=given):
                code, _, err = self.run_main('--local', str(given))
                self.assertEqual(code, 1)
                self.assertIn(str(given), err)
        self.assertEqual(self.linked(), [])

    def test_the_core_links_never_point_into_a_workspace(self):
        make_exec(self.local / 'brza-note')
        with patch.object(runtime, 'WORKSPACE', self.workspace):
            self.run_main('--all')
        self.assertEqual(self.target('brza-note'), self.core / 'brza-note')

    def test_the_second_run_changes_nothing(self):
        self.run_main('--all')
        code, out, _ = self.run_main('--all')
        self.assertEqual(code, 0)
        self.assertIn('já aponta', out)

    def test_a_missing_checkout_folder_is_refused_with_the_path(self):
        shutil.rmtree(self.client)
        code, _, err = self.run_main('--client')
        self.assertEqual(code, 1)
        self.assertIn(str(self.client), err)

    def test_run_and_sessions_link_only_those_from_the_client(self):
        make_exec(self.client / 'brza-sessions')
        self.assertEqual(self.run_main('--run', '--sessions')[0], 0)
        self.assertEqual(self.linked(), ['brza-run', 'brza-sessions'])
        self.assertEqual(self.target('brza-sessions'), self.client / 'brza-sessions')

    def test_a_linked_worktree_as_checkout_is_a_warning(self):
        (self.workflow / '.git').write_text('gitdir: /x\n')
        code, _, err = self.run_main('--all', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('worktree', err)


class RefusalAndRelinkTest(InstallCase):
    def test_a_link_to_another_place_is_refused_pointing_to_relink_and_nothing_is_linked(self):
        self.alien('brza-note')
        code, _, err = self.run_main('--all')
        self.assertEqual(code, 1)
        self.assertIn('brza-note', err)
        self.assertIn('--relink', err)
        self.assertEqual(self.linked(), ['brza-note'])
        self.assertEqual(self.target('brza-note'), Path('/tmp/outro'))

    def test_relink_swaps_the_link_that_points_elsewhere_and_links_the_rest(self):
        old = self.local / 'brza-note'
        self.alien('brza-note', str(old))
        code, out, _ = self.run_main('--all', '--relink')
        self.assertEqual(code, 0)
        self.assertEqual(self.target('brza-note'), self.core / 'brza-note')
        self.assertEqual(self.linked(), ['brza-install', 'brza-note', 'brza-where'])
        self.assertIn(str(old), out)

    def test_relink_also_swaps_a_dangling_link(self):
        self.alien('brza-where', str(self.base / 'sumiu' / 'brza-where'))
        self.assertEqual(self.run_main('--all', '--relink')[0], 0)
        self.assertEqual(self.target('brza-where'), self.core / 'brza-where')

    def test_relink_never_replaces_a_regular_file(self):
        self.bin.mkdir()
        (self.bin / 'brza-note').write_text('meu script\n')
        code, _, err = self.run_main('--all', '--relink')
        self.assertEqual(code, 1)
        self.assertIn('não é um link', err)
        self.assertEqual((self.bin / 'brza-note').read_text(), 'meu script\n')
        self.assertEqual(self.linked(), ['brza-note'])

    def test_relink_dry_run_changes_nothing_and_says_so(self):
        self.alien('brza-note')
        code, out, _ = self.run_main('--all', '--relink', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('brza-note', out)
        self.assertEqual(self.target('brza-note'), Path('/tmp/outro'))
        self.assertEqual(self.linked(), ['brza-note'])

    def test_dry_run_links_nothing(self):
        code, out, _ = self.run_main('--all', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('brza-where', out)
        self.assertFalse(self.bin.exists())

    def test_local_moves_the_command_that_pointed_elsewhere_only_with_relink(self):
        # a migração do P8: o brza-tree que apontava para outro lugar passa ao scripts/brza do workspace
        self.alien('brza-tree', str(self.base / 'velho' / 'brza-tree'))
        self.assertEqual(self.run_main('--local', str(self.workspace))[0], 1)
        self.assertEqual(self.run_main('--local', str(self.workspace), '--relink')[0], 0)
        self.assertEqual(self.target('brza-tree'), self.local / 'brza-tree')

    def test_the_same_name_from_two_places_in_one_run_is_refused(self):
        make_exec(self.local / 'brza-note')
        code, _, err = self.run_main('--all', '--local', str(self.workspace))
        self.assertEqual(code, 1)
        self.assertIn('brza-note', err)
        self.assertIn('dois lugares', err)
        self.assertEqual(self.linked(), [])


class ChatCommandTest(InstallCase):
    def test_dry_run_changes_nothing(self):
        calls = []
        with patch.object(runtime, 'rpc', lambda *a: calls.append(a)):
            code, _, _ = self.run_main('--chat-command', '--dry-run')
        self.assertEqual((code, calls, self.linked()), (0, [], []))

    def test_it_links_the_claude_brza_of_the_client_and_points_the_override_to_the_link(self):
        calls = []
        with patch.object(runtime, 'rpc', lambda *a: calls.append(a)):
            code, _, _ = self.run_main('--chat-command')
        self.assertEqual(code, 0)
        self.assertEqual(calls, [('settings.mutateAgentLaunch',
                                  {'type': 'command', 'agent': 'claude', 'value': str(self.bin / 'claude-brza')})])
        self.assertEqual(self.target('claude-brza'), self.client / 'claude-brza')

    def test_a_foreign_claude_brza_is_refused_and_relink_takes_it(self):
        self.alien('claude-brza', '/outro')
        calls = []
        with patch.object(runtime, 'rpc', lambda *a: calls.append(a)):
            code, _, err = self.run_main('--chat-command')
            self.assertEqual((code, calls), (1, []))
            self.assertIn('--relink', err)
            code, _, _ = self.run_main('--chat-command', '--relink')
        self.assertEqual(code, 0)
        self.assertEqual(self.target('claude-brza'), self.client / 'claude-brza')
        self.assertEqual(len(calls), 1)

    def test_an_app_that_does_not_answer_is_an_exit_with_the_hint(self):
        def down(*_):
            raise runtime.BrzaError('sem runtime')
        with patch.object(runtime, 'rpc', down):
            code, _, err = self.run_main('--chat-command')
        self.assertEqual(code, 1)
        self.assertIn('abra o BrzA Studio', err)


class ShowTest(InstallCase):
    def run_show(self):
        with patch.object(runtime, 'rpc', lambda *a, **k: {'settings': {'agentCmdOverrides': {'claude': ''}}}):
            return self.run_main()

    def test_it_lists_every_expected_link_with_its_state(self):
        self.run_main('--all')
        code, out, _ = self.run_show()
        self.assertEqual(code, 0)
        for name in ('brza-install', 'brza-note', 'brza-where'):
            self.assertIn(f'{name}: ok', out)
        for name in ('brza-doctor', 'brza-run', 'claude-brza'):
            self.assertIn(f'{name}: ausente', out)
        self.assertIn('agentCmdOverrides.claude', out)

    def test_expected_links_are_the_core_the_client_and_the_claude_brza_in_the_checkout(self):
        found = dict(INSTALL['expected_links']())
        self.assertEqual(sorted(found), ['brza-doctor', 'brza-install', 'brza-note', 'brza-run', 'brza-where',
                                         'claude-brza'])
        self.assertEqual(found['claude-brza'], self.client / 'claude-brza')
        self.assertEqual(found['brza-note'], self.core / 'brza-note')


class LocalScriptTest(InstallCase):
    """O que os scripts locais precisam para achar o brza_runtime: pelo BRZA_WORKFLOW ou pelo brza-note ligado."""

    def setUp(self):
        super().setUp()
        shutil.copy(BIN / 'brza_runtime.py', self.core / 'brza_runtime.py')
        shutil.copytree(ROOT / 'core' / '.claude' / 'hooks', self.workflow / 'core' / '.claude' / 'hooks',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.script = self.local / 'brza-app'
        make_exec(self.script, f'#!{sys.executable}\n{LOCAL_HEADER}print(runtime.__file__)\n')

    def run_script(self, env: dict):
        base = {'HOME': str(self.base), 'BRZA_REGISTRY': '/nonexistent/x.toml', 'PATH': f'{self.bin}:/usr/bin:/bin',
                'PYTHONDONTWRITEBYTECODE': '1'}
        return subprocess.run([str(self.script)], capture_output=True, text=True, cwd=str(self.base),
                              env={**base, **env})

    def test_the_header_finds_the_runtime_by_the_brza_workflow_variable(self):
        r = self.run_script({'BRZA_WORKFLOW': str(self.workflow)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(r.stdout.strip()).resolve(), (self.core / 'brza_runtime.py').resolve())

    def test_the_header_finds_the_runtime_by_the_real_path_of_the_linked_brza_note(self):
        self.assertEqual(self.run_main('--all')[0], 0)
        r = self.run_script({})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(r.stdout.strip()).resolve(), (self.core / 'brza_runtime.py').resolve())


class ProcessTest(unittest.TestCase):
    """O brza-install de verdade, com o HOME trocado: os links vão ao ~/.local/bin temporário e funcionam."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.env = {**{k: v for k, v in os.environ.items() if k not in ('BRZA_WORKFLOW', 'BRZA_WORKSPACE', 'ORCA_USER_DATA_PATH',
                                                         'ORCA_CLI_COMMAND')},
                    'HOME': str(self.home), 'BRZA_REGISTRY': str(self.home / 'registro.toml'),
                    'PYTHONDONTWRITEBYTECODE': '1'}

    def run_install(self, *argv):
        return subprocess.run([sys.executable, str(BIN / 'brza-install'), *argv], capture_output=True, text=True,
                              cwd=str(self.home), env=self.env)

    def test_all_client_and_relink_link_the_real_commands_and_the_links_run(self):
        r = self.run_install('--all', '--client', '--relink')
        self.assertEqual(r.returncode, 0, r.stderr)
        local_bin = self.home / '.local' / 'bin'
        for name, folder in (('brza-note', 'bin'), ('brza-where', 'bin'), ('brza-register', 'bin'),
                             ('brza-install', 'bin'), ('brza-doctor', 'packages/client/bin'),
                             ('brza-sessions', 'packages/client/bin'), ('brza-run', 'packages/client/bin')):
            self.assertEqual((local_bin / name).resolve(), (ROOT / folder / name).resolve(), name)
        self.assertFalse((local_bin / 'claude-brza').exists())
        where = subprocess.run([str(local_bin / 'brza-where')], capture_output=True, text=True, cwd=str(self.home),
                               env=self.env)
        self.assertEqual((where.returncode, where.stdout), (0, ''))
        for name in ('brza-note', 'brza-sessions', 'brza-doctor', 'brza-run', 'brza-install'):
            r = subprocess.run([str(local_bin / name), '--help'], capture_output=True, text=True, cwd=str(self.home),
                               env=self.env)
            self.assertEqual(r.returncode, 0, f'{name}: {r.stderr}')

    def test_the_shown_state_names_the_links_missing(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('brza-note: ausente', r.stdout)


if __name__ == '__main__':
    unittest.main()
