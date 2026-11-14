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
import shlex
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
import agent_runtime  # noqa: E402  o doctor já pôs o client/bin no path
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = DOCTOR['main'].__globals__
ORIGINAL_GIT_IDENT = GLOBALS['git_ident']
ORIGINAL_GIT_CONFIG_EMAIL = GLOBALS['git_config_email']
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
        self.ident = 'Anderson Pereira Brzezinski <anderson@outro-workspace.example> 1793166405 -0300'
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, 'identidade')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('anderson@outro-workspace.example', line)
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

    def test_env_cloud_without_socket_exits_zero_and_warns(self):
        self.ident = 'Outra Pessoa <outra@example.com> 1793166405 -0300'
        data = self.base / 'user-data-cloud'
        data.mkdir()
        missing_app = self.base / 'sem-app'
        env = {'CURSOR_CONVERSATION_ID': 'bc-teste', 'CURSOR_API_KEY': '', 'BRZA_CURSOR_API_KEY': '',
               'ORCA_USER_DATA_PATH': str(data)}
        with patch.dict(os.environ, env), patch.dict(GLOBALS, {'APP': missing_app}):
            code, out = self.run_doctor('--env')
        self.assertEqual(code, 0, out)
        self.assertNotIn('ERRO', out)
        self.assertTrue(self.line(out, 'socket').startswith('AVISO'), out)
        self.assertIn('cloud', self.line(out, 'socket'))
        self.assertTrue(self.line(out, 'app').startswith('AVISO'), out)
        self.assertTrue(self.line(out, 'identidade').startswith('AVISO'), out)
        self.assertTrue(self.line(out, 'chave').startswith('AVISO'), out)
        self.assertIn('ausente', self.line(out, 'chave'))

    def test_env_without_cloud_and_without_socket_is_an_error(self):
        data = self.base / 'user-data-mac'
        data.mkdir()
        env = {'CURSOR_CONVERSATION_ID': '', 'ORCA_USER_DATA_PATH': str(data)}
        with patch.dict(os.environ, env):
            code, out = self.run_doctor('--env')
        self.assertNotEqual(code, 0)
        line = self.line(out, 'socket')
        self.assertTrue(line.startswith('ERRO'), out)
        self.assertIn('ausente', line)

    def test_env_cloud_with_socket_does_not_warn_about_the_socket(self):
        data = self.base / 'user-data-socket'
        data.mkdir()
        (data / 'orca-runtime.json').write_text('{}')
        env = {'CURSOR_CONVERSATION_ID': 'bc-teste', 'CURSOR_API_KEY': '', 'BRZA_CURSOR_API_KEY': '',
               'ORCA_USER_DATA_PATH': str(data)}
        with patch.dict(os.environ, env):
            code, out = self.run_doctor('--env')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out, 'socket').startswith('ok'), out)
        self.assertIn('studio', self.line(out, 'runtime'))
        self.assertNotIn('cloud', self.line(out, 'runtime'))

    def test_env_socket_and_conversation_id_stay_studio_and_keep_the_identity_error(self):
        self.ident = 'Outra Pessoa <outra@example.com> 1793166405 -0300'
        data = self.base / 'user-data-both'
        data.mkdir()
        (data / 'orca-runtime.json').write_text('{}')
        env = {'CURSOR_CONVERSATION_ID': 'bc-teste', 'CURSOR_API_KEY': '', 'BRZA_CURSOR_API_KEY': '',
               'ORCA_USER_DATA_PATH': str(data)}
        with patch.dict(os.environ, env):
            code, out = self.run_doctor('--env')
        self.assertNotEqual(code, 0)
        self.assertIn('studio', self.line(out, 'runtime'))
        self.assertNotIn('cloud', self.line(out, 'runtime'))
        self.assertTrue(self.line(out, 'identidade').startswith('ERRO'), out)

    def test_env_treats_brza_cursor_api_key_as_present_without_printing_it(self):
        sentinel = 'sentinela-brza-chave-9c2e'
        data = self.base / 'user-data-key'
        data.mkdir()
        (data / 'orca-runtime.json').write_text('{}')
        env = {'CURSOR_CONVERSATION_ID': '', 'CURSOR_API_KEY': '', 'BRZA_CURSOR_API_KEY': sentinel,
               'ORCA_USER_DATA_PATH': str(data)}
        with patch.dict(os.environ, env):
            code, out = self.run_doctor('--env')
        line = self.line(out, 'chave')
        if sentinel in out or str(len(sentinel)) in line:
            self.fail('a chave ou o comprimento apareceu na saída')
        self.assertEqual(code, 0)
        self.assertTrue(line.startswith('ok'))
        self.assertNotIn('CURSOR_API_KEY', line)
        self.assertNotIn('exporte CURSOR_API_KEY', out)

    def test_false_key_is_not_printed_passed_or_stored_in_the_parent(self):
        """Sentinela falsa. A saída, o argv e o ambiente do pai não ficam com ela, nem em parte."""
        left, right = 'zq7Nm4kL9c2eQ8p1', 'w3rT6yH0b5vA2s8d'
        sentinel = left + right
        window = 8

        def leaked(blob) -> bool:
            text = blob if isinstance(blob, str) else ''
            if not text:
                return False
            if sentinel in text or left in text or right in text:
                return True
            for piece in (left, right):
                for index in range(0, len(piece) - window + 1):
                    if piece[index:index + window] in text:
                        return True
            return False

        def assert_clean(blob, where):
            if leaked(blob):
                self.fail(f'a chave apareceu em {where}')

        def run_captured(call):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = call()
            return code, out.getvalue(), err.getvalue()

        data = self.base / 'user-data-secret'
        data.mkdir()
        private = {'CURSOR_CONVERSATION_ID': 'bc-aaa', 'BRZA_CURSOR_API_KEY': sentinel, 'CURSOR_API_KEY': ''}
        with patch.dict(os.environ, {'ORCA_USER_DATA_PATH': str(data), 'CURSOR_CONVERSATION_ID': ''}):
            os.environ.pop('BRZA_CURSOR_API_KEY', None)
            os.environ.pop('CURSOR_API_KEY', None)
            before = dict(os.environ)
            chosen = agent_runtime.select_runtime(env=private)
            changed = sorted(key for key in set(os.environ) | set(before) if os.environ.get(key) != before.get(key))
            if changed:
                self.fail(f'o select_runtime alterou o ambiente do pai: {changed}')
            self.assertEqual(chosen.name, 'cloud')
            listed = json.dumps([record.as_dict() for record in chosen.list()], ensure_ascii=False)
            assert_clean(listed, 'list da seleção')
            self.assertNotIn('CURSOR_API_KEY', listed)

        argv_hits = []

        def watch(args):
            parts = [str(part) for part in args] if isinstance(args, (list, tuple)) else [str(args)]
            argv_hits.append(parts)
            if any(leaked(part) for part in parts):
                raise AssertionError('a chave foi parar num argv')

        def spy_call(real):
            def spy(*args, **kwargs):
                for arg in args:
                    if isinstance(arg, (list, tuple)):
                        watch(arg)
                    elif isinstance(arg, str):
                        watch([arg])
                return real(*args, **kwargs)
            return spy

        def spy_exec(*args, **kwargs):
            for arg in args:
                if isinstance(arg, (list, tuple)):
                    watch(arg)
                elif isinstance(arg, str):
                    watch([arg])
            raise AssertionError('exec no caminho')

        def spy_set(mapping, key, value):
            if leaked(value if isinstance(value, str) else ''):
                raise AssertionError('a chave foi gravada no ambiente do pai')
            return environ_set(mapping, key, value)

        def spy_putenv(name, value):
            if leaked(value if isinstance(value, str) else ''):
                raise AssertionError('a chave foi gravada no ambiente do pai')
            return os_putenv(name, value)

        environ_set = os._Environ.__setitem__
        os_putenv = os.putenv
        sessions = runpy.run_path(str(CLIENT / 'brza-sessions'))
        # O setUp troca o git. Aqui o doctor volta ao subprocess.run de produção, para o espião ver o argv.
        GLOBALS['git_ident'] = ORIGINAL_GIT_IDENT
        GLOBALS['git_config_email'] = ORIGINAL_GIT_CONFIG_EMAIL
        env = {'CURSOR_CONVERSATION_ID': 'bc-aaa', 'CURSOR_API_KEY': '', 'BRZA_CURSOR_API_KEY': sentinel,
               'ORCA_USER_DATA_PATH': str(data)}
        call_through = (
            'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
            'subprocess.Popen', 'subprocess.getoutput', 'subprocess.getstatusoutput', 'os.system',
        )
        originals = {
            'subprocess.run': subprocess.run, 'subprocess.call': subprocess.call,
            'subprocess.check_call': subprocess.check_call, 'subprocess.check_output': subprocess.check_output,
            'subprocess.Popen': subprocess.Popen, 'subprocess.getoutput': subprocess.getoutput,
            'subprocess.getstatusoutput': subprocess.getstatusoutput, 'os.system': os.system,
        }
        patches = [patch.dict(os.environ, env), patch.object(os._Environ, '__setitem__', spy_set),
                   patch('os.putenv', spy_putenv)]
        for name in call_through:
            patches.append(patch(name, spy_call(originals[name])))
        for name in ('os.execl', 'os.execle', 'os.execlp', 'os.execlpe', 'os.execv', 'os.execve', 'os.execvp',
                     'os.execvpe'):
            patches.append(patch(name, spy_exec))
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            parent_before = dict(os.environ)
            cloud_calls = (
                ('doctor --env', lambda: GLOBALS['main'](['--env']), None, 0),
                ('doctor --env --json', lambda: GLOBALS['main'](['--env', '--json']), None, 0),
                ('sessions list --json', lambda: sessions['main'](), ['brza-sessions', 'list', '--json'], 0),
                ('sessions read --json', lambda: sessions['main'](), ['brza-sessions', 'read', '--json'], 0),
                ('diag-cloud --dry-run', lambda: sessions['main'](),
                 ['brza-sessions', 'diag-cloud', '--agent', 'cursor', '--dry-run'], 0),
                ('diag-cloud', lambda: sessions['main'](), ['brza-sessions', 'diag-cloud', '--agent', 'cursor'], 1),
            )
            captured = {}
            for label, call, argv, expected in cloud_calls:
                if argv is None:
                    code, out, err = run_captured(call)
                else:
                    with patch.object(sys, 'argv', argv):
                        code, out, err = run_captured(call)
                captured[label] = out
                self.assertEqual(code, expected, label)
                assert_clean(out, label)
                assert_clean(err, f'stderr de {label}')
                if '--json' in label:
                    assert_clean(json.dumps(json.loads(out)), f'JSON de {label}')
            self.assertIn('abriria', captured['diag-cloud --dry-run'])
            for flag in ('--help', '-h'):
                with patch.object(sys, 'argv', ['brza-sessions', flag]):
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        with self.assertRaises(SystemExit) as caught:
                            sessions['main']()
                self.assertIn(caught.exception.code, (0, None))
                assert_clean(out.getvalue(), flag)
                assert_clean(err.getvalue(), f'stderr de {flag}')
            chosen = agent_runtime.select_runtime()
            self.assertEqual(chosen.name, 'cloud')
            assert_clean(json.dumps([record.as_dict() for record in chosen.list()]), 'select_runtime cloud')
            planned = sessions['open_argv']('grok-4.7', str(self.workspace), None)
            assert_clean(shlex.join(planned), 'open_argv')
            self.assertNotIn('--api-key', planned)
            self.assertNotIn('--resume', planned)
            (data / 'orca-runtime.json').write_text('{}')
            code, out, err = run_captured(lambda: GLOBALS['main'](['--env', '--json']))
            assert_clean(out, 'doctor studio')
            assert_clean(err, 'stderr do doctor studio')
            payload = json.loads(out)
            assert_clean(json.dumps(payload), 'JSON do doctor studio')
            runtime_line = next(item['message'] for item in payload if item['check'] == 'runtime')
            self.assertIn('studio', runtime_line)
            self.assertNotIn('cloud', runtime_line)
            studio = agent_runtime.select_runtime()
            self.assertEqual(studio.name, 'studio')
            with self.assertRaises(agent_runtime.BrzaError) as raised:
                studio.list()
            assert_clean(str(raised.exception), 'studio list')
            if os.environ.get('CURSOR_API_KEY', ''):
                self.fail('a chave foi gravada no ambiente do pai')
            for key, value in os.environ.items():
                if key == 'BRZA_CURSOR_API_KEY':
                    continue
                if leaked(value if isinstance(value, str) else ''):
                    self.fail('a chave foi gravada no ambiente do pai')
            if os.environ.get('BRZA_CURSOR_API_KEY') != parent_before.get('BRZA_CURSOR_API_KEY'):
                self.fail('a chave no ambiente do pai mudou')
        self.assertTrue(any('git' in tokens and 'var' in tokens for tokens in argv_hits))
        for parts in argv_hits:
            for part in parts:
                if leaked(part):
                    self.fail('a chave foi parar num argv')

    def test_conversation_id_does_not_soften_the_doctor_without_env(self):
        self.ident = 'Outra Pessoa <outra@example.com> 1793166405 -0300'
        with patch.dict(os.environ, {'CURSOR_CONVERSATION_ID': 'bc-teste'}):
            code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, 'identidade').startswith('ERRO'))


if __name__ == '__main__':
    unittest.main()
