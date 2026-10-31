"""Testes do `brza where` e do `brza register`, que rodam como processo, com o BRZA_REGISTRY de cada teste: nada lê o
~/.config real nem o brza-workspace real.

    python3 -m unittest discover -s tests/cli
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
IDENTITY = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
MANIFEST = 'schema = 1\n\n[workspace]\nname = "acme-notes"\nbranch = "trunk"\n\n[code]\nname = "acme-app"\npath = "{code}"\n'


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, env={**os.environ, **IDENTITY})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def make_repo(path: Path, branch: str, files: dict | None = None) -> Path:
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', branch)
    for name, text in (files or {'README.md': 'x\n'}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, 'add', '-A')
    git(path, 'commit', '-q', '-m', 'base')
    return path


class CliCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.registry = self.base / 'config' / 'brza' / 'workspaces.toml'
        self.code = make_repo(self.base / 'acme-app', 'main')
        self.notes = make_repo(self.base / 'acme-notes', 'trunk',
                               {'.brza/workspace.toml': MANIFEST.format(code=self.code)})
        self.far = self.base / 'elsewhere' / 'feat'
        git(self.code, 'worktree', 'add', '-q', '-b', 'feat/pln-001-x', str(self.far))

    def run_cli(self, name: str, *args: str, cwd: Path | None = None, env: dict | None = None, python: str | None = None):
        environ = {k: v for k, v in os.environ.items() if k not in ('BRZA_WORKSPACE', 'XDG_CONFIG_HOME')}
        environ['BRZA_REGISTRY'] = str(self.registry)
        environ.update(env or {})
        r = subprocess.run([python or sys.executable, str(BIN / name), *args], cwd=str(cwd or self.base), env=environ,
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def entries(self) -> str:
        return self.registry.read_text() if self.registry.exists() else ''


class WhereTest(CliCase):
    def test_a_worktree_outside_the_code_directory_with_no_registry_entry_prints_nothing(self):
        for cwd in (self.code, self.far):
            code, out, err = self.run_cli('brza-where', cwd=cwd)
            self.assertEqual((code, out, err), (0, '', ''), cwd)

    def test_with_the_entry_the_code_checkout_and_the_outside_worktree_are_the_code_of_the_workspace(self):
        self.assertEqual(self.run_cli('brza-register', str(self.notes), '--code', str(self.code))[0], 0)
        (self.far / 'sub').mkdir()
        for cwd in (self.code, self.far, self.far / 'sub'):
            self.assertEqual(self.run_cli('brza-where', cwd=cwd)[1], f'{self.notes} code\n', cwd)

    def test_the_workspace_checkout_and_its_worktrees_are_notes_without_the_registry(self):
        git(self.notes, 'branch', 'workstream/a')
        tree = self.notes / '.worktrees' / 'a'
        git(self.notes, 'worktree', 'add', '-q', str(tree), 'workstream/a')
        for cwd in (self.notes, tree):
            self.assertEqual(self.run_cli('brza-where', cwd=cwd)[1:], (f'{self.notes} notes\n', ''), cwd)
        self.assertFalse(self.registry.exists())

    def test_the_other_places_print_nothing(self):
        other = make_repo(self.base / 'fixfy', 'master')
        for cwd in (other, self.base):
            self.assertEqual(self.run_cli('brza-where', cwd=cwd)[:2], (0, ''), cwd)

    def test_the_override_is_honored_and_the_unrelated_cwd_still_prints_nothing(self):
        other = make_repo(self.base / 'fixfy', 'master')
        env = {'BRZA_WORKSPACE': str(self.notes)}
        self.assertEqual(self.run_cli('brza-where', cwd=other, env=env)[:2], (0, ''))
        self.assertEqual(self.run_cli('brza-where', cwd=self.notes, env=env)[1], f'{self.notes} notes\n')

    @unittest.skipUnless(Path('/usr/bin/python3').exists(), 'sem o python3 do sistema')
    def test_it_runs_on_the_system_python(self):
        self.assertEqual(self.run_cli('brza-where', cwd=self.notes, python='/usr/bin/python3')[1],
                         f'{self.notes} notes\n')
        self.assertEqual(self.run_cli('brza-register', str(self.notes), python='/usr/bin/python3')[0], 0)
        self.assertEqual(self.run_cli('brza-where', cwd=self.far, python='/usr/bin/python3')[1], f'{self.notes} code\n')


class RegisterTest(CliCase):
    def test_it_writes_the_pair_and_creates_the_registry_folder(self):
        code, out, err = self.run_cli('brza-register', str(self.notes), '--code', str(self.code))
        self.assertEqual(code, 0, err)
        self.assertIn(f'registro: {self.registry}', out)
        self.assertIn(f'root = "{self.notes}"', self.entries())
        self.assertIn(f'code = "{self.code}"', self.entries())

    def test_without_code_it_takes_the_manifest_default(self):
        code, _, err = self.run_cli('brza-register', str(self.notes))
        self.assertEqual(code, 0, err)
        self.assertIn(f'code = "{self.code}"', self.entries())

    def test_the_code_given_as_a_worktree_is_stored_as_the_main_checkout(self):
        code, _, err = self.run_cli('brza-register', str(self.notes), '--code', str(self.far))
        self.assertEqual(code, 0, err)
        self.assertIn(f'code = "{self.code}"', self.entries())

    def test_it_refuses_a_code_already_linked_to_another_workspace(self):
        self.assertEqual(self.run_cli('brza-register', str(self.notes))[0], 0)
        before = self.entries()
        second = make_repo(self.base / 'second-notes', 'master', {'.brza/workspace.toml': 'schema = 1\n'})
        code, out, err = self.run_cli('brza-register', str(second), '--code', str(self.far))
        self.assertEqual(code, 1)
        self.assertIn(str(self.notes), out + err)
        self.assertIn('já está ligado', out + err)
        self.assertEqual(self.entries(), before)

    def test_the_same_pair_twice_is_a_no_op(self):
        self.assertEqual(self.run_cli('brza-register', str(self.notes))[0], 0)
        before = self.entries()
        code, out, _ = self.run_cli('brza-register', str(self.notes))
        self.assertEqual(code, 0)
        self.assertIn('já registrado', out)
        self.assertEqual(self.entries(), before)

    def test_the_same_workspace_with_another_code_is_refused(self):
        self.assertEqual(self.run_cli('brza-register', str(self.notes))[0], 0)
        other = make_repo(self.base / 'other-app', 'main')
        before = self.entries()
        code, out, err = self.run_cli('brza-register', str(self.notes), '--code', str(other))
        self.assertEqual(code, 1)
        self.assertIn(str(self.code), out + err)
        self.assertEqual(self.entries(), before)

    def test_it_refuses_a_root_without_manifest_and_a_code_that_is_not_a_git_repo(self):
        plain = self.base / 'plain'
        plain.mkdir()
        code, out, err = self.run_cli('brza-register', str(plain), '--code', str(self.code))
        self.assertEqual(code, 1)
        self.assertIn('workspace.toml', out + err)
        code, out, err = self.run_cli('brza-register', str(self.notes), '--code', str(plain))
        self.assertEqual(code, 1)
        self.assertFalse(self.registry.exists())

    def test_it_keeps_what_was_already_in_the_registry(self):
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text('# meu comentário\n[[workspace]]\nroot = "/velho"\ncode = "/velho-code"\n')
        self.assertEqual(self.run_cli('brza-register', str(self.notes))[0], 0)
        text = self.entries()
        self.assertTrue(text.startswith('# meu comentário\n[[workspace]]\nroot = "/velho"\n'))
        self.assertIn(f'root = "{self.notes}"', text)

    def test_the_written_registry_is_what_where_reads(self):
        self.run_cli('brza-register', str(self.notes))
        self.assertEqual(self.run_cli('brza-where', cwd=self.far)[1], f'{self.notes} code\n')

    def test_a_broken_registry_is_not_overwritten(self):
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text('[[workspace\n')
        code, out, err = self.run_cli('brza-register', str(self.notes))
        self.assertEqual(code, 1)
        self.assertEqual(self.registry.read_text(), '[[workspace\n')


class NoteFromTheCodeTest(CliCase):
    """O `brza note` de um worktree do código fora do diretório dele acha o workspace pelo registro, e o workstream pelo
    plano da branch — sem BRZA_WORKSPACE e sem BRZA_STUDIO."""

    def setUp(self):
        super().setUp()
        git(self.notes, 'branch', 'workstream/w')
        self.tree = self.notes / '.worktrees' / 'w'
        git(self.notes, 'worktree', 'add', '-q', str(self.tree), 'workstream/w')
        (self.tree / 'plans' / 'PLN-001-x').mkdir(parents=True)
        (self.tree / 'plans' / 'PLN-001-x' / 'PLN-001-plan.md').write_text('# plano\n')
        git(self.tree, 'add', '-A')
        git(self.tree, 'commit', '-q', '-m', 'plano')

    def note(self, cwd: Path):
        return self.run_cli('brza-note', 'new', 'issue', 'a-issue', cwd=cwd, env={'BRZA_STUDIO': ''})

    def test_with_the_registry_entry_the_issue_lands_in_the_workstream_of_the_plan(self):
        self.assertEqual(self.run_cli('brza-register', str(self.notes))[0], 0)
        code, out, err = self.note(self.far)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out.strip(), str(self.tree / 'work' / 'issues' / 'a-issue.md'))

    def test_without_the_entry_it_refuses(self):
        code, out, err = self.note(self.far)
        self.assertEqual(code, 1)
        self.assertIn('recusado', out + err)
        self.assertFalse((self.tree / 'work' / 'issues' / 'a-issue.md').exists())

    def test_from_the_workspace_worktree_it_needs_no_registry(self):
        code, out, err = self.note(self.tree)
        self.assertEqual(code, 0, out + err)
        self.assertFalse(self.registry.exists())


if __name__ == '__main__':
    unittest.main()
