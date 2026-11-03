"""Testes do `brza quota` (D6/M7): a saída humana e a `--json` a partir da fixture do `orca account list`.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import unittest
from pathlib import Path

from mcp_support import ENTRY, HomeCase

from brza_mcp import accounts as acc  # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'orca-account-list.json').read_text())


def load():
    path = str(ENTRY.parent / 'brza-quota')
    loader = importlib.machinery.SourceFileLoader('brza_quota', path)
    spec = importlib.util.spec_from_loader('brza_quota', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Reader:
    def __init__(self, payload=FIXTURE):
        self.payload = payload

    def read(self):
        return acc.parse(self.payload)


class QuotaTest(HomeCase):
    def run_quota(self, *argv, reader=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = load().main(list(argv), reader=reader or Reader(), db=':memory:')
        return code, out.getvalue(), err.getvalue()

    def test_the_human_output_shows_each_window_with_reset_and_projection(self):
        code, out, _ = self.run_quota()
        self.assertEqual(code, 0)
        self.assertIn('systemDefault', out)
        self.assertIn('session', out)
        self.assertIn('usado 10%', out)
        self.assertIn('usado 29%', out)
        self.assertIn('projeção 10%', out)  # uma amostra só: a projeção é o usado
        self.assertIn('sem uso', out)  # as duas contas gerenciadas da fixture não têm leitura

    def test_json_is_the_report_the_limits_projection_feeds(self):
        code, out, _ = self.run_quota('--json')
        self.assertEqual(code, 0)
        report = json.loads(out)['accounts']
        default = next(a for a in report if a['account'] == 'systemDefault')
        self.assertEqual([(w['name'], w['used'], w['resets_at'], w['projected']) for w in default['windows']],
                         [('session', 10, 1793701800586, 10.0), ('weekly', 29, 1794114000586, 29.0),
                          ('fableWeekly', 0, 1794114000000, 0.0)])
        self.assertEqual(default['cap'], 90.0)
        self.assertEqual(sum(1 for a in report if not a['windows']), 2)

    def test_no_client_exits_3(self):
        class Down:
            def read(self):
                raise acc.AccountError('nenhum cliente de pé')
        code, out, err = self.run_quota(reader=Down())
        self.assertEqual((code, out), (3, ''))
        self.assertIn('nenhum cliente', err)


if __name__ == '__main__':
    unittest.main()
