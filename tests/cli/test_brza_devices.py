"""Testes do brza-devices, com o simctl, o adb, o avdmanager, o sdkmanager e o emulador trocados por falsos, e o SDK e
os AVDs num diretório temporário.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import os
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
DEVICES = runpy.run_path(str(ROOT / 'packages' / 'client' / 'bin' / 'brza-devices'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = DEVICES['main'].__globals__
IOS_27_0, IOS_27_1 = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0', 'com.apple.CoreSimulator.SimRuntime.iOS-27-1'
TYPE = 'com.apple.CoreSimulator.SimDeviceType.'
IMAGE = 'system-images;android-36.1;google_apis_playstore;arm64-v8a'


def simctl(devices=None, extra_types=()):
    return {
        'devicetypes': [{'name': n, 'identifier': TYPE + n.replace(' ', '-')}
                        for n in ('iPhone Duo', 'iPhone 18 Pro', 'iPhone 17', 'iPhone 8', *extra_types)],
        'runtimes': [
            {'name': 'iOS 27.0', 'version': '27.0', 'identifier': IOS_27_0, 'platform': 'iOS', 'isAvailable': True,
             'supportedDeviceTypes': [{'name': 'iPhone 18 Pro', 'identifier': TYPE + 'iPhone-18-Pro'},
                                      {'name': 'iPhone 17', 'identifier': TYPE + 'iPhone-17'}]},
            {'name': 'iOS 27.1', 'version': '27.1', 'identifier': IOS_27_1, 'platform': 'iOS', 'isAvailable': True,
             'supportedDeviceTypes': [{'name': 'iPhone Duo', 'identifier': TYPE + 'iPhone-Duo'}]},
            {'name': 'iOS 18.0', 'version': '18.0', 'identifier': 'gone', 'platform': 'iOS', 'isAvailable': False,
             'supportedDeviceTypes': []},
        ],
        'devices': devices if devices is not None else {
            IOS_27_1: [{'udid': 'DUO', 'name': 'iPhone Duo', 'state': 'Shutdown', 'isAvailable': True}],
            IOS_27_0: [{'udid': 'S17', 'name': 'iPhone 17', 'state': 'Booted', 'isAvailable': True}],
            'gone': [{'udid': 'OLD', 'name': 'iPhone 8', 'state': 'Shutdown', 'isAvailable': False}],
        },
    }


class Fake:
    """O run falso: responde cada ferramenta pelo começo dos argumentos e guarda as chamadas."""

    def __init__(self, state=None, adb='List of devices attached\n\n', avd_names=None, profiles='pixel_10_pro\n',
                 sdk_list='', fail=(), sysctl=None):
        machine = {'hw.memsize': str(24 * 2 ** 30), 'hw.perflevel0.physicalcpu': '5'}
        self.sysctl = sysctl if sysctl is not None else machine
        self.state = state if state is not None else simctl()
        self.adb, self.avd_names, self.profiles, self.sdk_list, self.fail = adb, avd_names or {}, profiles, \
            sdk_list, fail
        self.calls, self.inputs = [], []

    def __call__(self, args, *, input=None, timeout=120, env=None, capture=True):
        self.calls.append(args)
        self.inputs.append(input)
        name = Path(args[0]).name
        out, code = '', 0
        if args[:3] == ['xcrun', 'simctl', 'list']:
            out = json.dumps(self.state)
        elif args[:3] == ['xcrun', 'simctl', 'create']:
            out = 'NEW-UDID\n'
        elif name == 'adb' and args[1:] == ['devices']:
            out = self.adb
        elif name == 'adb' and args[3:] == ['emu', 'avd', 'name']:
            out = self.avd_names.get(args[2], '') + '\nOK\n'
        elif name == 'adb' and args[3:5] == ['shell', 'getprop']:
            out = '1\n'
        elif name == 'avdmanager' and args[1:3] == ['list', 'device']:
            out = self.profiles
        elif name == 'avdmanager' and args[1:3] == ['create', 'avd']:
            folder = Path(os.environ['ANDROID_AVD_HOME']) / f'{args[4]}.avd'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'config.ini').write_text('hw.gpu.mode=auto\nhw.ramSize=2048\nhw.lcd.width=1080\n')
        elif args[0] == 'sysctl':
            out = self.sysctl.get(args[2], '')
            code = 0 if out else 1
        elif name == 'sdkmanager' and args[1:] == ['--list']:
            out = self.sdk_list
        if any(f in ' '.join(args) for f in self.fail):
            code = 1
        return subprocess.CompletedProcess(args, code, out, 'erro')


class DevicesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.sdk, self.avds = base / 'sdk', base / 'avd'
        for rel in ('platform-tools/adb', 'emulator/emulator', 'cmdline-tools/latest/bin/avdmanager',
                    'cmdline-tools/latest/bin/sdkmanager'):
            (self.sdk / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.sdk / rel).write_text('')
        self.avds.mkdir()
        self.env = {'ANDROID_HOME': str(self.sdk), 'ANDROID_AVD_HOME': str(self.avds), 'JAVA_HOME': '/java'}
        self.spawned = []

    def add_image(self, package=IMAGE):
        _, api, tag, abi = package.split(';')
        folder = self.sdk / 'system-images' / api / tag / abi
        folder.mkdir(parents=True)
        (folder / 'system.img').write_text('')

    def add_avd(self, name='Pixel_9_Pro_XL', label='Pixel 9 Pro XL'):
        folder = self.avds / f'{name}.avd'
        folder.mkdir()
        (self.avds / f'{name}.ini').write_text(f'path={folder}\ntarget=android-36.1\n')
        (folder / 'config.ini').write_text(f'avd.ini.displayname={label}\n'
                                           'image.sysdir.1=system-images/android-36.1/google_apis_playstore/arm64-v8a/\n')

    def run_main(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()
        spawn = lambda cmd, log: self.spawned.append(cmd)  # noqa: E731
        with patch.dict(GLOBALS, run=fake, spawn=spawn), patch.dict(os.environ, self.env), \
                patch.object(GLOBALS['shutil'], 'which', lambda tool: '/usr/bin/' + tool), \
                patch.object(GLOBALS['platform'], 'machine', lambda: 'arm64'), \
                patch.object(GLOBALS['time'], 'sleep', lambda _: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                DEVICES['main'](list(args))
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()
        return 0, out.getvalue(), err.getvalue()

    # --- list

    def test_list_shows_devices_what_can_be_created_and_hides_unavailable_runtimes(self):
        self.add_image()
        self.add_avd()
        code, out, _ = self.run_main(Fake(), 'list')
        self.assertEqual(code, 0)
        self.assertIn('● iPhone 17', out)
        self.assertIn('○ iPhone Duo', out)
        self.assertNotIn('iPhone 8', out)
        self.assertIn('para criar no iOS 27.0: iPhone 18 Pro', out)
        self.assertNotIn('para criar no iOS 27.1', out)
        self.assertIn('○ Pixel 9 Pro XL', out)
        self.assertIn(f'AVD novo: um perfil do avdmanager, com {IMAGE}', out)

    def test_list_json_and_running_emulator(self):
        self.add_avd()
        fake = Fake(adb='List of devices attached\nemulator-5554\tdevice\n\n',
                    avd_names={'emulator-5554': 'Pixel_9_Pro_XL'})
        code, out, _ = self.run_main(fake, '--json', '--android')
        data = json.loads(out)
        self.assertNotIn('ios', data)
        self.assertEqual(data['android']['devices'][0]['serial'], 'emulator-5554')
        self.assertIsNone(data['android']['image'])

    def test_profiles_come_from_avdmanager_with_the_java_env(self):
        code, out, _ = self.run_main(Fake(profiles='pixel_10_pro\npixel_9\n'), 'list', '--android', '--profiles')
        self.assertIn('perfis: pixel_10_pro, pixel_9', out)

    # --- open de um aparelho que existe

    def test_open_existing_simulator_boots_and_shows_the_window(self):
        fake = Fake()
        code, out, _ = self.run_main(fake, 'open', 'iphone duo')
        self.assertEqual(code, 0)
        self.assertIn(['xcrun', 'simctl', 'boot', 'DUO'], fake.calls)
        self.assertIn(['open', '-a', 'Simulator', '--args', '-CurrentDeviceUDID', 'DUO'], fake.calls)
        self.assertIn('iPhone Duo: ligado (iOS 27.1, DUO)', out)
        self.assertNotIn(['xcrun', 'simctl', 'bootstatus', 'DUO'], fake.calls)

    def test_open_booted_simulator_only_brings_the_window_and_wait_uses_bootstatus(self):
        fake = Fake()
        code, out, _ = self.run_main(fake, 'open', 'S17', '--wait')
        self.assertNotIn(['xcrun', 'simctl', 'boot', 'S17'], fake.calls)
        self.assertIn(['xcrun', 'simctl', 'bootstatus', 'S17'], fake.calls)
        self.assertIn('iPhone 17: já estava ligado, pronto', out)

    def test_open_existing_avd_spawns_the_emulator_on_a_free_port(self):
        self.add_avd()
        fake = Fake(adb='List of devices attached\nemulator-5554\tdevice\n\n', avd_names={'emulator-5554': 'Outro'})
        code, out, _ = self.run_main(fake, 'open', 'Pixel 9 Pro XL', '--wait')
        self.assertEqual(code, 0)
        self.assertEqual(self.spawned, [[str(self.sdk / 'emulator' / 'emulator'), '-avd', 'Pixel_9_Pro_XL',
                                         '-port', '5556']])
        self.assertIn([str(self.sdk / 'platform-tools' / 'adb'), '-s', 'emulator-5556', 'wait-for-device'],
                      fake.calls)
        self.assertIn('Pixel 9 Pro XL: ligado, pronto (android-36.1, emulator-5556)', out)

    def test_running_avd_is_not_spawned_again(self):
        self.add_avd()
        fake = Fake(adb='List of devices attached\nemulator-5554\tdevice\n\n',
                    avd_names={'emulator-5554': 'Pixel_9_Pro_XL'})
        code, out, _ = self.run_main(fake, 'open', 'pixel_9_pro_xl')
        self.assertEqual(self.spawned, [])
        self.assertIn('já estava ligado (android-36.1, emulator-5554)', out)

    def test_open_without_name_takes_the_only_device_or_lists(self):
        code, out, _ = self.run_main(Fake(simctl({IOS_27_1: [
            {'udid': 'DUO', 'name': 'iPhone Duo', 'state': 'Shutdown'}]})), 'open', '--ios')
        self.assertIn('iPhone Duo: ligado', out)
        self.add_avd()
        code, _, _ = self.run_main(Fake(), 'open')
        self.assertIn('mais de um aparelho', str(code))
        self.assertIn('Pixel 9 Pro XL (Android, android-36.1, desligado, Pixel_9_Pro_XL)', str(code))

    # --- open de um modelo ou perfil

    def test_ios_model_is_created_in_the_newest_runtime_that_takes_it(self):
        fake = Fake()
        code, out, _ = self.run_main(fake, 'open', 'iPhone 18 Pro')
        self.assertEqual(code, 0)
        self.assertIn(['xcrun', 'simctl', 'create', 'iPhone 18 Pro', TYPE + 'iPhone-18-Pro', IOS_27_0], fake.calls)
        self.assertIn(['xcrun', 'simctl', 'boot', 'NEW-UDID'], fake.calls)
        self.assertIn('iPhone 18 Pro: criado e ligado (iOS 27.0, NEW-UDID)', out)

    def test_ios_model_without_runtime_points_to_download(self):
        fake = Fake()
        code, _, _ = self.run_main(fake, 'open', 'iPhone 8', '--ios')
        self.assertIn('nenhum runtime iOS instalado aceita o iPhone 8', str(code))
        self.assertIn('xcodebuild -downloadPlatform iOS', str(code))
        self.assertFalse(any(c[0] == 'xcodebuild' for c in fake.calls))

    def test_ios_download_runs_xcodebuild_with_the_version_and_retries(self):
        after = simctl()
        after['runtimes'][0]['supportedDeviceTypes'].append({'name': 'iPhone 8', 'identifier': TYPE + 'iPhone-8'})
        fake = Fake()
        states = iter([simctl(), after])
        original = fake.__call__

        def call(args, **kw):
            if args[:3] == ['xcrun', 'simctl', 'list']:
                fake.state = next(states)
            return original(args, **kw)
        code, out, err = self.run_main(call, 'open', 'iPhone 8', '--ios', '--download', '--runtime', '26.0')
        self.assertEqual(code, 0, code)
        self.assertIn(['xcodebuild', '-downloadPlatform', 'iOS', '-buildVersion', '26.0'], fake.calls)
        self.assertIn('iPhone 8: criado e ligado', out)

    def test_runtime_needs_download(self):
        code, _, err = self.run_main(Fake(), 'open', 'iPhone 8', '--runtime', '26.0')
        self.assertEqual(code, 2)
        self.assertIn('--runtime só vale com --download', err)

    def test_android_profile_creates_the_avd_with_the_best_installed_image(self):
        self.add_image()
        self.add_image('system-images;android-35;google_apis;arm64-v8a')
        self.add_image('system-images;android-37;google_apis_playstore;x86_64')
        fake = Fake()
        code, out, _ = self.run_main(fake, 'open', 'Pixel 10 Pro', '--android')
        self.assertEqual(code, 0, code)
        create = next(c for c in fake.calls if c[1:3] == ['create', 'avd'])
        self.assertEqual(create[3:], ['-n', 'Pixel_10_Pro', '-k', IMAGE, '-d', 'pixel_10_pro'])
        self.assertEqual(fake.inputs[fake.calls.index(create)], 'no\n')
        self.assertEqual(self.spawned[0][1:3], ['-avd', 'Pixel_10_Pro'])
        self.assertIn('Pixel 10 Pro: criado e ligado (android-36.1, emulator-5554)', out)
        config = (self.avds / 'Pixel_10_Pro.avd' / 'config.ini').read_text()
        self.assertEqual(config, 'hw.gpu.mode=host\nhw.ramSize=4096\nhw.lcd.width=1080\nhw.cpu.ncore=6\n')

    def test_android_profile_without_image_points_to_download(self):
        code, _, _ = self.run_main(Fake(), 'open', 'pixel_10_pro', '--android')
        self.assertIn('nenhuma imagem Android arm64-v8a instalada', str(code))
        self.assertEqual(self.spawned, [])

    def test_android_download_takes_the_newest_play_image(self):
        listing = ('  system-images;android-35;google_apis_playstore;arm64-v8a | 1 | Play\n'
                   '  system-images;android-36.1;google_apis_playstore;arm64-v8a | 2 | Play\n'
                   '  system-images;android-36.1;google_apis;arm64-v8a | 2 | APIs\n')
        fake = Fake(sdk_list=listing)
        code, out, _ = self.run_main(fake, 'open', 'Pixel 10 Pro', '--android', '--download')
        self.assertEqual(code, 0, code)
        download = next(c for c in fake.calls if Path(c[0]).name == 'sdkmanager' and c[1] != '--list')
        self.assertEqual(download[1:], [IMAGE])
        self.assertTrue(fake.inputs[fake.calls.index(download)].startswith('y\n'))

    def test_unknown_name_says_so(self):
        code, _, _ = self.run_main(Fake(), 'open', 'Nada Disso')
        self.assertIn('"Nada Disso" não é aparelho criado, modelo iOS nem perfil Android', str(code))

    def test_failed_boot_is_reported(self):
        code, _, _ = self.run_main(Fake(fail=('simctl boot',)), 'open', 'iPhone Duo')
        self.assertIn('o simctl não ligou iPhone Duo', str(code))

    def test_without_sdk_android_is_explained(self):
        self.env['ANDROID_HOME'] = str(self.sdk / 'nada')
        with patch.object(Path, 'home', lambda: self.sdk / 'nada'):
            code, out, _ = self.run_main(Fake(), 'list', '--android')
        self.assertIn('sem o SDK', out)


class TuningTest(unittest.TestCase):
    def tuning(self, sysctl, cpus=15):
        fake = Fake(sysctl=sysctl)
        with patch.dict(GLOBALS, run=fake), patch.object(GLOBALS['os'], 'cpu_count', lambda: cpus), \
                patch.object(GLOBALS['shutil'], 'which', lambda tool: '/usr/sbin/' + tool):
            return DEVICES['host_tuning']()

    def test_half_the_ram_up_to_4_gb_and_the_performance_cores_plus_one(self):
        self.assertEqual(self.tuning({'hw.memsize': str(24 * 2 ** 30), 'hw.perflevel0.physicalcpu': '5'}),
                         {'hw.gpu.mode': 'host', 'hw.ramSize': '4096', 'hw.cpu.ncore': '6'})
        self.assertEqual(self.tuning({'hw.memsize': str(6 * 2 ** 30), 'hw.perflevel0.physicalcpu': '4'}, cpus=4),
                         {'hw.gpu.mode': 'host', 'hw.ramSize': '3072', 'hw.cpu.ncore': '4'})

    def test_without_sysctl_answers_falls_back_to_2_gb_and_half_the_cores(self):
        self.assertEqual(self.tuning({}, cpus=8), {'hw.gpu.mode': 'host', 'hw.ramSize': '2048', 'hw.cpu.ncore': '4'})

    def test_tune_replaces_and_appends_and_a_missing_config_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.ini'
            config.write_text('a=1\nhw.gpu.mode=auto\n')
            DEVICES['tune_avd'](config, {'hw.gpu.mode': 'host', 'hw.ramSize': '4096'})
            self.assertEqual(config.read_text(), 'a=1\nhw.gpu.mode=host\nhw.ramSize=4096\n')
            with self.assertRaisesRegex(DEVICES['DevicesError'], 'não li'):
                DEVICES['tune_avd'](Path(tmp) / 'nada' / 'config.ini', {})


class HelpersTest(unittest.TestCase):
    def test_norm_and_versions(self):
        norm, key = DEVICES['norm'], DEVICES['version_key']
        self.assertEqual(norm('Pixel_9_Pro_XL'), norm('pixel 9  pro xl'))
        self.assertGreater(key('android-36.1'), key('android-36'))
        self.assertLess(key('android-Baklava'), key('android-35'))

    def test_best_image_prefers_the_machine_abi_play_and_newest(self):
        best = DEVICES['best_image']
        with patch.object(DEVICES['main'].__globals__['platform'], 'machine', lambda: 'arm64'):
            self.assertEqual(best(['system-images;android-36;google_apis;arm64-v8a',
                                   'system-images;android-35;google_apis_playstore;arm64-v8a',
                                   'system-images;android-37;google_apis_playstore;x86_64']),
                             'system-images;android-35;google_apis_playstore;arm64-v8a')
            self.assertIsNone(best(['system-images;android-37;default;x86_64']))


if __name__ == '__main__':
    unittest.main()
