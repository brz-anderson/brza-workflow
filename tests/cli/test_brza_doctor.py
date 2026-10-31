"""Testes do brza-doctor, num workspace de mentira: o scripts/brza e o .claude/settings.json num diretório temporário,
o ~/.local/bin temporário, o ambiente e o `rpc` trocados, e um brza-studio com uma origin/master de verdade.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import os
import plistlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
CLIENT = ROOT / 'packages' / 'client' / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402

DOCTOR = runpy.run_path(str(CLIENT / 'brza-doctor'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = DOCTOR['main'].__globals__
NOREPLY = '240569289+brz-anderson@users.noreply.github.com'
IDENT = f'Anderson Pereira Brzezinski <{NOREPLY}> 1793166405 -0300'


def git(*args, cwd: Path) -> str:
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class DoctorTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

        self.workspace = self.base / 'brza-workspace'
        scripts = self.workspace / 'scripts' / 'brza'
        scripts.mkdir(parents=True)
        for name in ('brza-run', 'brza-tree', 'claude-brza'):
            (scripts / name).write_text('#!/bin/sh\n')
            (scripts / name).chmod(0o755)
        (self.workspace / '.claude').mkdir()
        self.settings = self.workspace / '.claude' / 'settings.json'
        self.settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [
            {'type': 'command', 'command': 'python3 "${BRZA_WORKSPACE}/.claude/hooks/measure.py" turn || true'}]}]}}))

        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name in ('brza-run', 'brza-tree', 'claude-brza'):
            (self.bin / name).symlink_to(scripts / name)

        self.studio = self.base / 'brza-studio'
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(self.studio)], check=True)
        git('commit', '-q', '--allow-empty', '-m', 'um', cwd=self.studio)
        self.old = git('rev-parse', 'HEAD', cwd=self.studio)
        git('commit', '-q', '--allow-empty', '-m', 'dois', cwd=self.studio)
        git('commit', '-q', '--allow-empty', '-m', 'três', cwd=self.studio)
        self.tip = git('rev-parse', 'HEAD', cwd=self.studio)
        git('update-ref', 'refs/remotes/origin/master', self.tip, cwd=self.studio)

        self.app = self.base / 'Applications' / 'BrzA Studio.app'
        self.make_app(self.tip[:12])
        self.dev_profile = self.base / 'brza-dev'
        self.dev_profile.mkdir()
        (self.dev_profile / 'orca-data.json').write_text('{}')

        self.ident = IDENT
        self.override = str(self.bin / 'claude-brza')
        self.rpc_error = None
        self.pnpm = '/opt/homebrew/bin/pnpm'
        for p in (patch.object(runtime, 'WORKSPACE', self.workspace),
                  patch.object(runtime, 'STUDIO', self.studio),
                  patch.object(runtime, 'LOCAL_BIN', self.bin),
                  patch.object(runtime, 'rpc', self.fake_rpc),
                  patch.dict(os.environ, {'BRZA_WORKSPACE': str(self.workspace)}),
                  patch.dict(GLOBALS, {'APP': self.app, 'DEV_PROFILE': self.dev_profile,
                                       'git_ident': lambda kind: self.ident,
                                       'which': lambda name: self.pnpm if name == 'pnpm' else None})):
            p.start()
            self.addCleanup(p.stop)

    def make_app(self, commit: str):
        (self.app / 'Contents').mkdir(parents=True, exist_ok=True)
        with open(self.app / 'Contents' / 'Info.plist', 'wb') as f:
            plistlib.dump({'CFBundleShortVersionString': f'1.4.242-alpha.1.local.1793166405233.{commit}'}, f)

    def fake_rpc(self, method, params=None, timeout=10.0):
        self.assertEqual(method, 'settings.getAgentLaunch')
        if self.rpc_error:
            raise runtime.BrzaError(self.rpc_error)
        return {'settings': {'agentCmdOverrides': {'claude': self.override}}}

    def run_doctor(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = GLOBALS['main'](list(argv))
        return code, out.getvalue()

    def line(self, output: str, check: str) -> str:
        found = [ln for ln in output.splitlines() if f' {check}:' in ln]
        self.assertTrue(found, f'sem linha de {check} em:\n{output}')
        return found[0]

    def test_tudo_certo_sai_0_e_diz_o_que_conferiu(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertNotIn('ERRO', out)
        self.assertNotIn('AVISO', out)
        for check in ('identidade', 'medição', 'links', 'chat', 'pnpm', 'perfil', 'app'):
            self.assertTrue(self.line(out, check).startswith('ok'), out)

    def test_identidade_errada_e_erro_com_o_conserto(self):
        self.ident = 'Anderson Pereira Brzezinski <anderson@fixfy.com.br> 1793166405 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'identidade')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('anderson@fixfy.com.br', line)
        self.assertIn('conserta:', out)
        self.assertIn('claude-brza', out.split(line, 1)[1].splitlines()[1])

    def test_committer_errado_tambem_e_erro(self):
        GLOBALS['git_ident'] = lambda kind: IDENT if kind == 'AUTHOR' else 'x <x@x> 1 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('GIT_COMMITTER_IDENT', self.line(out, 'identidade'))

    def test_sem_brza_workspace_no_ambiente_e_erro_de_medicao(self):
        del os.environ['BRZA_WORKSPACE']
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'medição')
        self.assertTrue(line.startswith('ERRO'))
        self.assertIn('BRZA_WORKSPACE', line)

    def test_settings_sem_o_measure_e_erro_de_medicao(self):
        self.settings.write_text('{"hooks": {}}')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('measure.py', self.line(out, 'medição'))

    def test_settings_ausente_e_erro_de_medicao(self):
        self.settings.unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, 'medição').startswith('ERRO'))

    def test_link_ausente_e_erro_que_manda_o_install_all(self):
        (self.bin / 'brza-tree').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'links')
        self.assertTrue(line.startswith('ERRO'))
        self.assertIn('brza-tree ausente', line)
        self.assertIn('brza install --all', out)

    def test_link_alheio_e_erro_que_manda_apagar_antes(self):
        (self.bin / 'brza-run').unlink()
        (self.bin / 'brza-run').symlink_to('/tmp/outro')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('brza-run aponta para', self.line(out, 'links'))
        self.assertIn(f'rm {self.bin / "brza-run"}', out)

    def test_override_vazio_e_erro_que_manda_o_chat_command(self):
        self.override = ''
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, 'chat').startswith('ERRO'))
        self.assertIn('brza install --chat-command', out)

    def test_override_certo_sem_o_link_do_claude_brza_e_erro(self):
        (self.bin / 'claude-brza').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('claude-brza ausente', self.line(out, 'chat'))

    def test_app_que_nao_responde_e_aviso_e_nao_erro(self):
        self.rpc_error = 'o runtime não respondeu'
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out, 'chat').startswith('AVISO'))

    def test_pnpm_fora_do_path_e_aviso(self):
        self.pnpm = None
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(self.line(out, 'pnpm').startswith('AVISO'))

    def test_perfil_brza_dev_sem_estado_e_aviso(self):
        (self.dev_profile / 'orca-data.json').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(self.line(out, 'perfil').startswith('AVISO'))

    def test_app_mais_velho_que_a_origin_master_e_aviso_com_os_commits(self):
        self.make_app(self.old[:12])
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        line = self.line(out, 'app')
        self.assertTrue(line.startswith('AVISO'), out)
        self.assertIn('2 commits', line)
        self.assertIn('brza app install', out)

    def test_app_de_commit_desconhecido_e_aviso(self):
        self.make_app('0123456789ab')
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(self.line(out, 'app').startswith('AVISO'))

    def test_app_ausente_e_aviso(self):
        (self.app / 'Contents' / 'Info.plist').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(self.line(out, 'app').startswith('AVISO'))

    def test_json_traz_cada_conferencia_e_a_mesma_saida(self):
        self.override = ''
        code, out = self.run_doctor('--json')
        self.assertEqual(code, 1)
        checks = json.loads(out)
        by_name = {c['check']: c for c in checks}
        self.assertEqual(by_name['chat']['level'], 'ERRO')
        self.assertIn('brza install --chat-command', by_name['chat']['fix'])
        self.assertEqual(by_name['identidade']['level'], 'ok')
        self.assertIsNone(by_name['identidade']['fix'])

    def test_conferencia_que_quebra_vira_erro_e_nao_derruba_as_outras(self):
        def boom(kind):
            raise OSError('sem git')
        GLOBALS['git_ident'] = boom
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('sem git', self.line(out, 'identidade'))
        self.assertTrue(self.line(out, 'app').startswith('ok'))


if __name__ == '__main__':
    unittest.main()
