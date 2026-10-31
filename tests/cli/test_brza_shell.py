"""Teste do bloco do shell do pacote do cliente (packages/client/shell/brza.zsh, D10 do PLN-006): o zsh de verdade,
sem ~/.zshrc, com o brza-where e o brza-agents reais, o registro num diretório temporário e um `claude` de mentira.
Fora de workspace, a função cai na `brza_claude_fallback` do usuário, se existir, e senão no `claude` de verdade.

    python3 -m unittest discover -s tests/cli
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
BLOCK = ROOT / 'packages' / 'client' / 'shell' / 'brza.zsh'
IDENTITY = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
MANIFEST = 'schema = 1\n\n[workspace]\nname = "acme notes"\n\n[code]\nname = "acme-app"\npath = "{code}"\n'
FAKE_CLAUDE = f'#!{sys.executable}\nimport json, os, sys\nprint(json.dumps([sys.argv[1:], os.environ.get("BRZA_WORKSPACE")]))\n'


def init_repo(path: Path):
    path.mkdir(parents=True)
    subprocess.run(['git', '-C', str(path), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-q', '--allow-empty', '-m', 'x'], check=True,
                   env={**os.environ, **IDENTITY})


@unittest.skipUnless(shutil.which('zsh'), 'sem zsh')
class ShellBlockTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        # a raiz com espaço no nome: o brza-where imprime "<raiz> notes|code", e a função não pode parti-la
        self.workspace, self.code = self.base / 'acme notes', self.base / 'acme-app'
        init_repo(self.code)
        init_repo(self.workspace)
        (self.workspace / '.brza').mkdir()
        (self.workspace / '.brza' / 'workspace.toml').write_text(MANIFEST.format(code=self.code))
        agents = self.workspace / '.claude' / 'agents'
        agents.mkdir(parents=True)
        (agents / 'reviewer.md').write_text('---\ndescription: Revisa\nmodel: opus\n---\nRevise.\n')
        registry = self.base / 'registro.toml'
        registry.write_text(f'[[workspace]]\nroot = "{self.workspace}"\ncode = "{self.code}"\n')
        self.other = self.base / 'outro'
        init_repo(self.other)

        links, real, py = self.base / 'links', self.base / 'real', self.base / 'py'
        for folder in (links, real, py):
            folder.mkdir()
        (py / 'python3').symlink_to(sys.executable)
        (links / 'brza-where').symlink_to(BIN / 'brza-where')
        (links / 'brza-agents').symlink_to(BIN / 'brza-agents')
        (real / 'claude').write_text(FAKE_CLAUDE)
        (real / 'claude').chmod(0o755)
        self.env = {'HOME': str(self.base), 'PATH': f'{links}:{real}:{py}:/usr/bin:/bin', 'ZDOTDIR': str(self.base),
                    'BRZA_REGISTRY': str(registry), 'PYTHONDONTWRITEBYTECODE': '1'}

    def claude(self, cwd: Path, *argv: str, env: dict | None = None, fallback: bool = True):
        hook = 'brza_claude_fallback() { echo "FALLBACK $*"; }; ' if fallback else ''
        script = f'source "{BLOCK}"; {hook}claude "$@"'
        r = subprocess.run(['zsh', '-f', '-c', script, 'zsh', *argv], capture_output=True, text=True, cwd=str(cwd),
                           env={**self.env, **(env or {})})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def settings(self) -> list[str]:
        return ['--settings', str(self.workspace / '.claude' / 'settings.json')]

    def test_the_workspace_gets_the_settings_and_the_root_and_no_agents(self):
        out = json.loads(self.claude(self.workspace, '-p', 'oi'))
        self.assertEqual(out, [[*self.settings(), '-p', 'oi'], str(self.workspace)])

    def test_the_code_gets_the_settings_the_root_and_the_agents_of_the_workspace(self):
        argv, root = json.loads(self.claude(self.code, '-p', 'oi'))
        self.assertEqual(argv[:4], [*self.settings(), '--agents', argv[3]])
        self.assertEqual((argv[4:], root), (['-p', 'oi'], str(self.workspace)))
        self.assertEqual(list(json.loads(argv[3])), ['reviewer'])

    def test_the_agents_the_user_passed_win(self):
        for given in (['--agents', '{}'], ['--agents={}']):
            argv, _ = json.loads(self.claude(self.code, *given, '-p', 'oi'))
            self.assertEqual(argv, [*self.settings(), *given, '-p', 'oi'])

    def test_a_repository_that_is_neither_goes_to_the_fallback_hook_of_the_user(self):
        self.assertEqual(self.claude(self.other, '-p', 'oi'), 'FALLBACK -p oi')
        self.assertEqual(self.claude(self.base, '-p', 'oi'), 'FALLBACK -p oi')

    def test_without_the_hook_it_goes_to_the_real_claude_with_no_settings_and_no_root(self):
        for cwd in (self.other, self.base):
            argv, root = json.loads(self.claude(cwd, '-p', 'oi', fallback=False))
            self.assertEqual((argv, root), (['-p', 'oi'], None))

    def test_the_hook_does_not_run_inside_a_workspace_or_its_code(self):
        for cwd in (self.workspace, self.code):
            self.assertNotIn('FALLBACK', self.claude(cwd, '-p', 'oi'))

    def test_the_exported_variable_is_an_override_of_the_root(self):
        out = json.loads(self.claude(self.code, '-p', 'oi', env={'BRZA_WORKSPACE': str(self.workspace)}))
        self.assertEqual(out[1], str(self.workspace))

    def test_the_block_has_no_fixed_name(self):
        text = BLOCK.read_text()
        for fixed in ('brza-workspace', 'brza-studio', 'BRZA_STUDIO', 'Projects/', 'brza_where'):
            self.assertNotIn(fixed, text)

    def test_the_block_calls_nothing_of_fixfy(self):
        self.assertNotIn('fixfy', BLOCK.read_text().lower())


if __name__ == '__main__':
    unittest.main()
