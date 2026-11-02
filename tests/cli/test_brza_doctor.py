"""Testes do brza-doctor, num workspace de mentira: o checkout do brza-workflow, o manifesto, o registro e o
.claude/settings.json num diretório temporário, o ~/.local/bin temporário, o ambiente e o `rpc` trocados, e um
brza-studio com uma origin/master de verdade.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import os
import plistlib
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
CLIENT = ROOT / 'packages' / 'client' / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402

DOCTOR = runpy.run_path(str(CLIENT / 'brza-doctor'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = DOCTOR['main'].__globals__
EMAIL = 'dev@acme.example'
IDENT = f'Alguém <{EMAIL}> 1793166405 -0300'
MANIFEST = ('schema = 1\n\n[workspace]\nname = "acme-notes"\n\n[author]\nname = "Alguém"\nemail = "{email}"\n\n'
            '[code]\nname = "acme-app"\npath = "{code}"\n')


def make_exec(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n')
    path.chmod(0o755)


def git(*args, cwd: Path) -> str:
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class DoctorTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

        self.workflow = self.base / 'brza-workflow'
        self.core, self.client = self.workflow / 'bin', self.workflow / 'packages' / 'client' / 'bin'
        for folder, names in ((self.core, ('brza-install', 'brza-note')),
                              (self.client, ('brza-doctor', 'brza-run', 'claude-brza'))):
            for name in names:
                make_exec(folder / name)
        (self.core / 'brza_runtime.py').write_text('')

        self.studio = self.base / 'brza-studio'
        self.workspace = self.base / 'brza-workspace'
        self.workspace.mkdir()
        (self.workspace / '.brza').mkdir()
        self.manifest = self.workspace / '.brza' / 'workspace.toml'
        self.manifest.write_text(MANIFEST.format(email=EMAIL, code=self.studio))
        self.local = self.workspace / 'scripts' / 'brza'
        make_exec(self.local / 'brza-tree')
        (self.workspace / '.claude').mkdir()
        self.settings = self.workspace / '.claude' / 'settings.json'
        self.settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [
            {'type': 'command', 'command': 'python3 "${BRZA_WORKSPACE}/.claude/hooks/measure.py" turn || true'}]}]}}))

        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for folder, names in ((self.core, ('brza-install', 'brza-note')),
                              (self.client, ('brza-doctor', 'brza-run', 'claude-brza')),
                              (self.local, ('brza-tree',))):
            for name in names:
                (self.bin / name).symlink_to(folder / name)

        subprocess.run(['git', 'init', '-q', '-b', 'master', str(self.studio)], check=True)
        git('commit', '-q', '--allow-empty', '-m', 'um', cwd=self.studio)
        self.old = git('rev-parse', 'HEAD', cwd=self.studio)
        git('commit', '-q', '--allow-empty', '-m', 'dois', cwd=self.studio)
        git('commit', '-q', '--allow-empty', '-m', 'três', cwd=self.studio)
        self.tip = git('rev-parse', 'HEAD', cwd=self.studio)
        git('update-ref', 'refs/remotes/origin/master', self.tip, cwd=self.studio)
        self.registry = self.base / 'config' / 'workspaces.toml'
        self.register(self.workspace, self.studio)

        self.app = self.base / 'Applications' / 'BrzA Studio.app'
        self.make_app(self.tip[:12])
        self.dev_profile = self.base / 'brza-dev'
        self.dev_profile.mkdir()
        (self.dev_profile / 'orca-data.json').write_text('{}')

        self.ident = IDENT
        self.config_email = None
        self.override = str(self.bin / 'claude-brza')
        self.rpc_error = None
        self.pnpm = '/opt/homebrew/bin/pnpm'
        for p in (patch.object(runtime, 'WORKSPACE', self.workspace),
                  patch.object(runtime, 'STUDIO', self.studio),
                  patch.object(runtime, 'LOCAL_BIN', self.bin),
                  patch.object(runtime, 'rpc', self.fake_rpc),
                  patch.dict(os.environ, {'BRZA_WORKSPACE': str(self.workspace), 'BRZA_WORKFLOW': str(self.workflow),
                                          'BRZA_REGISTRY': str(self.registry)}),
                  patch.dict(GLOBALS, {'APP': self.app, 'DEV_PROFILE': self.dev_profile,
                                       'git_ident': lambda kind: self.ident,
                                       'git_config_email': lambda: self.config_email,
                                       'which': lambda name: self.pnpm if name == 'pnpm' else None})):
            p.start()
            self.addCleanup(p.stop)

    def register(self, root: Path, code: Path):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry, 'a') as f:
            f.write(f'[[workspace]]\nroot = "{root}"\ncode = "{code}"\n')

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
        for check in ('identidade', 'medição', 'links', 'registro', 'chat', 'pnpm', 'perfil', 'app'):
            self.assertTrue(self.line(out, check).startswith('ok'), out)

    def test_sem_workspace_o_que_precisa_dele_e_erro_e_o_resto_segue(self):  # A5: sem o legado de nome fixo
        with patch.object(runtime, 'WORKSPACE', None):
            code, out = self.run_doctor()
        self.assertEqual(code, 1)
        for check in ('identidade', 'medição', 'registro'):
            line = self.line(out, check)
            self.assertTrue(line.startswith('ERRO'), out)
            self.assertIn('nenhum workspace achado', line)
        self.assertTrue(self.line(out, 'links').startswith('ok'), out)

    def test_identidade_certa_vem_do_author_do_manifesto(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        line = self.line(out, 'identidade')
        self.assertTrue(line.startswith('ok'), out)
        self.assertIn(EMAIL, line)
        self.assertIn('[author].email', line)

    def test_identidade_errada_e_erro_com_o_conserto(self):
        self.ident = 'Anderson Pereira Brzezinski <anderson@fixfy.com.br> 1793166405 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'identidade')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('anderson@fixfy.com.br', line)
        self.assertIn(EMAIL, line)
        self.assertIn('conserta:', out)
        self.assertIn('claude-brza', out.split(line, 1)[1].splitlines()[1])

    def test_committer_errado_tambem_e_erro(self):
        GLOBALS['git_ident'] = lambda kind: IDENT if kind == 'AUTHOR' else 'x <x@x> 1 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('GIT_COMMITTER_IDENT', self.line(out, 'identidade'))

    def test_o_noreply_do_brz_anderson_nao_e_fixo(self):
        self.ident = 'Anderson <240569289+brz-anderson@users.noreply.github.com> 1793166405 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn(EMAIL, self.line(out, 'identidade'))
        self.manifest.write_text(MANIFEST.format(email='240569289+brz-anderson@users.noreply.github.com',
                                                 code=self.studio))
        code, out = self.run_doctor()
        self.assertTrue(self.line(out, 'identidade').startswith('ok'), out)

    def test_sem_author_no_manifesto_vale_o_git_config(self):
        self.manifest.write_text('schema = 1\n\n[code]\npath = "%s"\n' % self.studio)
        self.config_email = EMAIL
        code, out = self.run_doctor()
        line = self.line(out, 'identidade')
        self.assertTrue(line.startswith('ok'), out)
        self.assertIn('git config', line)
        self.config_email = 'outro@acme.example'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, 'identidade').startswith('ERRO'), out)

    def test_sem_author_nem_git_config_e_aviso_que_manda_gravar_o_author(self):
        self.manifest.write_text('schema = 1\n\n[code]\npath = "%s"\n' % self.studio)
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out, 'identidade').startswith('AVISO'), out)
        self.assertIn('[author].email', out.split(self.line(out, 'identidade'), 1)[1].splitlines()[1])

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

    def test_link_ausente_do_nucleo_e_erro_que_manda_o_install_all(self):
        (self.bin / 'brza-note').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'links')
        self.assertTrue(line.startswith('ERRO'))
        self.assertIn('brza-note ausente', line)
        self.assertIn('brza install --all', out)
        self.assertNotIn('--client', out)

    def test_link_ausente_do_cliente_e_do_local_mandam_o_comando_de_cada_um(self):
        (self.bin / 'brza-run').unlink()
        (self.bin / 'brza-tree').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'links')
        self.assertIn('brza-run ausente', line)
        self.assertIn('brza-tree ausente', line)
        fix = out.split(line, 1)[1].splitlines()[1]
        self.assertIn('brza install --client', fix)
        self.assertIn(f'--local {self.workspace}', fix)
        self.assertNotIn('--all', fix)

    def test_link_alheio_e_erro_que_manda_o_relink(self):
        (self.bin / 'brza-run').unlink()
        (self.bin / 'brza-run').symlink_to('/tmp/outro')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('brza-run aponta para', self.line(out, 'links'))
        self.assertIn('--relink', out)

    def test_link_para_o_workspace_no_lugar_do_checkout_e_erro(self):
        # o estado antes do P8: o brza-note ainda ligado ao scripts/brza do workspace
        make_exec(self.local / 'brza-note')
        (self.bin / 'brza-note').unlink()
        (self.bin / 'brza-note').symlink_to(self.local / 'brza-note')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('brza-note aponta para', self.line(out, 'links'))
        self.assertIn('brza install --all --relink', out)

    def test_comando_local_com_o_mesmo_nome_do_nucleo_vale_o_do_nucleo(self):
        make_exec(self.local / 'brza-note')
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out, 'links').startswith('ok'))

    def test_link_sem_alvo_fora_do_esperado_e_erro_que_manda_apagar(self):
        (self.bin / 'brza-velho').symlink_to(self.base / 'sumiu')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn('brza-velho sem alvo', self.line(out, 'links'))
        self.assertIn(f'rm {self.bin / "brza-velho"}', out)

    def test_workspace_sem_scripts_brza_nao_tem_link_local_a_conferir(self):
        shutil.rmtree(self.local)
        (self.bin / 'brza-tree').unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)

    def test_workspace_nao_registrado_com_codigo_no_manifesto_e_erro_que_manda_o_register(self):
        self.registry.unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'registro')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn(str(self.workspace), line)
        self.assertIn(f'brza register {self.workspace}', out)

    def test_workspace_sem_codigo_no_manifesto_nao_precisa_de_registro(self):
        self.registry.unlink()
        self.manifest.write_text(f'schema = 1\n\n[author]\nemail = "{EMAIL}"\n')
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out, 'registro').startswith('ok'))

    def test_codigo_ligado_a_dois_workspaces_e_erro(self):
        other = self.base / 'outro-ws'
        (other / '.brza').mkdir(parents=True)
        (other / '.brza' / 'workspace.toml').write_text('schema = 1\n')
        self.register(other, self.studio)
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'registro')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('dois workspaces', line)
        self.assertIn(str(other), line)

    def test_registro_ilegivel_e_erro(self):
        self.registry.write_text('[[workspace]\nroot = ')
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'registro')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('ilegível', line)

    def test_entrada_com_workspace_ou_codigo_que_sumiu_e_aviso(self):
        self.register(self.base / 'sumiu-ws', self.base / 'sumiu-code')
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        line = self.line(out, 'registro')
        self.assertTrue(line.startswith('AVISO'), out)
        self.assertIn('sumiu-ws', line)

    def test_workspace_sem_manifesto_e_aviso(self):
        self.manifest.unlink()
        code, out = self.run_doctor()
        self.assertTrue(self.line(out, 'registro').startswith('AVISO'), out)
        self.assertTrue(self.line(out, 'identidade').startswith('AVISO'), out)

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
