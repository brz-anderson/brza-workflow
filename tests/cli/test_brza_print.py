"""Testes do brza-print, com o simctl, o adb, o osascript e o screencapture trocados por falsos.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
CLIENT = ROOT / 'packages' / 'client' / 'bin'
PRINT = runpy.run_path(str(CLIENT / 'brza-print'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = PRINT['main'].__globals__
PrintError = PRINT['PrintError']
PNG = b'\x89PNG\r\n\x1a\n' + b'\0\0\0\rIHDR' + (390).to_bytes(4, 'big') + (844).to_bytes(4, 'big') + b'\0' * 8

SIMCTL = json.dumps({'devices': {
    'com.apple.CoreSimulator.SimRuntime.iOS-26-0': [
        {'udid': 'AAA', 'name': 'iPhone 18 Pro', 'state': 'Booted'},
        {'udid': 'BBB', 'name': 'iPad', 'state': 'Shutdown'},
    ]}})
ADB = 'List of devices attached\nemulator-5554  device product:sdk model:Pixel_9 transport_id:1\nR58 unauthorized\n\n'
WINDOWS = json.dumps([
    {'owner': 'Dock', 'id': 1, 'layer': 20, 'width': 100, 'height': 100},
    {'owner': 'BrzA Studio', 'id': 7858, 'layer': 0, 'width': 1440, 'height': 900},
    {'owner': 'BrzA Studio', 'id': 7851, 'layer': 0, 'width': 800, 'height': 600},
    {'owner': 'Google Chrome', 'id': 9, 'layer': 0, 'width': 1, 'height': 1},
])


class Fake:
    """O run falso: devolve a saída de cada ferramenta e grava o PNG onde a ferramenta gravaria."""

    def __init__(self, simctl=SIMCTL, adb=ADB, windows=WINDOWS, png=PNG, fail=()):
        self.simctl, self.adb, self.windows, self.png, self.fail, self.calls = simctl, adb, windows, png, fail, []

    def __call__(self, args, binary=False):
        self.calls.append(args)
        tool = args[0] if args[0] != 'xcrun' else args[2]
        code = 1 if tool in self.fail else 0
        out = ''
        if args[:3] == ['xcrun', 'simctl', 'list']:
            out = self.simctl
        elif args[:2] == ['adb', 'devices']:
            out = self.adb
        elif args[0] == 'osascript':
            out = self.windows
        elif args[:3] == ['xcrun', 'simctl', 'io'] and not code:
            Path(args[-1]).write_bytes(self.png)
        elif args[0] == 'screencapture' and not code:
            Path(args[-1]).write_bytes(self.png)
        elif args[0] == 'adb':
            out = b'' if code else self.png
        return subprocess.CompletedProcess(args, code, out, b'erro' if binary else 'erro')


class PrintTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.dest = self.base / 'PLN-001-C1.png'

    def run_main(self, fake: Fake, *args, which=True) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(GLOBALS, run=fake), patch.object(GLOBALS['shutil'], 'which', lambda _: which or None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                PRINT['main']([str(self.dest), *args])
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()
        return 0, out.getvalue(), err.getvalue()

    def test_two_devices_open_need_a_choice(self):
        code, _, err = self.run_main(Fake())
        self.assertIn('mais de um aparelho', str(code))
        self.assertIn('ios iPhone 18 Pro (AAA)', str(code))
        self.assertIn('android Pixel_9 (emulator-5554)', str(code))
        self.assertFalse(self.dest.exists())

    def test_the_only_device_is_found_alone(self):
        fake = Fake(adb='List of devices attached\n\n')
        code, out, _ = self.run_main(fake)
        self.assertEqual(code, 0)
        self.assertIn(['xcrun', 'simctl', 'io', 'AAA', 'screenshot', '--type=png'], [c[:6] for c in fake.calls])
        self.assertEqual(self.dest.read_bytes(), PNG)
        self.assertIn('390×844 px, simulador iOS', out)

    def test_android_bytes_come_from_screencap(self):
        fake = Fake()
        code, out, _ = self.run_main(fake, '--android')
        self.assertEqual(code, 0)
        self.assertIn(['adb', '-s', 'emulator-5554', 'exec-out', 'screencap', '-p'], fake.calls)
        self.assertEqual(self.dest.read_bytes(), PNG)

    def test_app_takes_the_front_window_ignoring_case(self):
        fake = Fake()
        code, out, _ = self.run_main(fake, '--app', 'brza studio')
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls[-1][:5], ['screencapture', '-x', '-o', '-l', '7858'])
        self.assertIn('janela do brza studio', out)

    def test_app_without_window_lists_the_apps(self):
        code, _, _ = self.run_main(Fake(), '--app', 'Google Chrome')
        self.assertIn('nenhuma janela aberta do app "Google Chrome"', str(code))
        self.assertIn('BrzA Studio', str(code))

    def test_failed_window_capture_points_to_the_permission(self):
        code, _, _ = self.run_main(Fake(fail=('screencapture',)), '--app', 'BrzA Studio')
        self.assertIn('Gravação de Tela', str(code))
        self.assertFalse(self.dest.exists())

    def test_named_device_must_be_open(self):
        code, _, _ = self.run_main(Fake(), '--ios', 'ZZZ')
        self.assertIn('simulador iOS ZZZ não está aberto; os abertos: iPhone 18 Pro (AAA)', str(code))

    def test_nothing_open_suggests_the_app_flag(self):
        code, _, _ = self.run_main(Fake(), which=False)
        self.assertIn('use --app NOME', str(code))

    def test_not_a_png_leaves_no_file(self):
        code, _, _ = self.run_main(Fake(png=b'texto'), '--ios')
        self.assertIn('não saiu como PNG', str(code))
        self.assertFalse(self.dest.exists())

    def test_existing_destination_is_refused_without_force(self):
        self.dest.write_bytes(b'antigo')
        code, _, err = self.run_main(Fake(), '--ios')
        self.assertEqual(code, 2)
        self.assertIn('já existe; use --force', err)
        code, _, _ = self.run_main(Fake(), '--ios', '--force')
        self.assertEqual((code, self.dest.read_bytes()), (0, PNG))

    def test_only_one_target_flag(self):
        code, _, err = self.run_main(Fake(), '--ios', '--app', 'x')
        self.assertEqual(code, 2)
        self.assertIn('not allowed with', err)


if __name__ == '__main__':
    unittest.main()
