"""Testes do desempatador (D4, M11): a tabela, o `tev1` e o Haiku, com o Ollama e o `claude` trocados por dublês.

Nenhuma chamada real ao `claude` (custa dinheiro) nem ao Ollama.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import unittest
from pathlib import Path

from mcp_support import PACKAGE  # noqa: F401  (põe o brza_mcp no sys.path)

from brza_mcp import resolver as res  # noqa: E402

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'm11_cases.json').read_text())
BY_TEXT = {c['text']: c['layer'] for c in CASES['layers']}


class FakeOllama:
    """O `/v1/systemone` dublê: responde a camada do caso do M11 com a confiança dada; ou levanta `error`."""

    def __init__(self, confidence=0.9, error=None, choice=None):
        self.confidence, self.error, self.choice, self.calls = confidence, error, choice, []

    def __call__(self, url, body, timeout):
        self.calls.append((url, body, timeout))
        if self.error:
            raise self.error
        choice = self.choice or BY_TEXT.get(body['state'], 'media')
        return {'model': body['model'], 'answers': {'layer': {'type': 'choice', 'choice': choice,
                                                              'probabilities': {choice: 0.8},
                                                              'confidence': self.confidence}}}


class FakeClaude:
    def __init__(self, error=None, code=0, out=None):
        self.error, self.code, self.out, self.calls = error, code, out, []

    def __call__(self, argv, input, env, timeout):  # noqa: A002
        self.calls.append((argv, input, env, timeout))
        if self.error:
            raise self.error
        choice = BY_TEXT.get(input.split('Tarefa:\n', 1)[-1], 'media')
        return self.code, self.out if self.out is not None else json.dumps({'structured_output': {'choice': choice}})


def make(config=None, ollama=None, claude=None):
    cfg = {'resolver.tev1_min_confidence': None, **(config or {})}
    return res.Resolver(config=lambda: cfg, ollama=ollama or FakeOllama(), claude=claude or FakeClaude())


class RoutingTest(unittest.TestCase):
    def test_without_a_threshold_everything_goes_to_haiku(self):
        ollama, claude = FakeOllama(confidence=0.99), FakeClaude()
        out = make(ollama=ollama, claude=claude).classify('Listar os arquivos .md de uma pasta')
        self.assertEqual((out['path'], out['layer']), ('haiku', 'rapida'))
        self.assertEqual(out['tev1_confidence'], 0.99)  # o tev1 ainda roda, para a calibração da fase 2b
        self.assertEqual(len(ollama.calls), 1)
        self.assertEqual(len(claude.calls), 1)
        self.assertIn('resolver.tev1_min_confidence', out['reason'])

    def test_with_a_threshold_and_high_confidence_tev1_answers(self):
        claude = FakeClaude()
        out = make({'resolver.tev1_min_confidence': 0.5}, FakeOllama(0.8), claude).classify(CASES['layers'][0]['text'])
        self.assertEqual((out['path'], out['layer'], out['confidence']), ('tev1', 'alta', 0.8))
        self.assertEqual(claude.calls, [])

    def test_below_the_threshold_goes_to_haiku(self):
        claude = FakeClaude()
        out = make({'resolver.tev1_min_confidence': 0.5}, FakeOllama(0.3), claude).classify('Contar linhas do log')
        self.assertEqual(out['path'], 'haiku')
        self.assertEqual(len(claude.calls), 1)
        self.assertEqual(out['tev1_confidence'], 0.3)

    def test_without_ollama_goes_straight_to_haiku_and_does_not_hang(self):
        for error in (ConnectionRefusedError('recusou'), TimeoutError('demorou'), OSError('sem rota')):
            claude = FakeClaude()
            out = make({'resolver.tev1_min_confidence': 0.5}, FakeOllama(error=error), claude).classify('x')
            self.assertEqual(out['path'], 'haiku', error)
            self.assertIsNone(out['tev1_confidence'])
            self.assertEqual(len(claude.calls), 1)

    def test_an_ollama_answer_out_of_format_goes_to_haiku(self):
        for bad in ({'error': 'model is required'}, {'answers': {}}, {'answers': {'layer': {'choice': 'xyz',
                                                                                              'confidence': 1}}}):
            claude = FakeClaude()
            out = make({'resolver.tev1_min_confidence': 0.1}, lambda *a: bad, claude).classify('x')
            self.assertEqual(out['path'], 'haiku', bad)

    def test_without_claude_the_table_stays(self):
        for claude in (FakeClaude(error=FileNotFoundError('claude')), FakeClaude(code=1), FakeClaude(out='não é json'),
                       FakeClaude(out=json.dumps({'structured_output': {'choice': 'xyz'}}))):
            out = make({'resolver.tev1_min_confidence': 0.5}, FakeOllama(error=OSError('x')), claude).classify('x')
            self.assertEqual((out['path'], out['layer']), ('fallback', None))
            self.assertIn('fica a tabela', out['reason'])

    def test_every_answer_carries_the_path_the_confidence_and_the_time(self):
        ticks = iter([10.0, 10.25])
        r = res.Resolver(config=lambda: {'resolver.tev1_min_confidence': 0.5}, ollama=FakeOllama(0.7),
                         claude=FakeClaude(), clock=lambda: next(ticks))
        out = r.classify('Revisar de forma adversária o plano de migração antes da execução')
        self.assertEqual(set(out), {'layer', 'path', 'confidence', 'ms', 'reason', 'tev1_confidence'})
        self.assertEqual((out['path'], out['confidence'], out['ms']), ('tev1', 0.7, 250))

    def test_ollama_gets_the_keep_alive_the_model_and_the_url_of_the_keys(self):
        ollama = FakeOllama()
        make({'resolver.tev1_keep_alive': '1m', 'resolver.tev1_model': 'tev1:4b',
              'resolver.ollama_url': 'http://127.0.0.1:9/'}, ollama).classify('texto da tarefa')
        url, body, _ = ollama.calls[0]
        self.assertEqual(url, 'http://127.0.0.1:9/v1/systemone')
        self.assertEqual((body['model'], body['keep_alive'], body['state']), ('tev1:4b', '1m', 'texto da tarefa'))
        question = body['questions']['layer']
        self.assertEqual(question['type'], 'choice')
        self.assertEqual(set(question['criteria']), {'alta', 'media', 'rapida'})

    def test_claude_gets_the_h2_command_with_the_budget_of_the_key_and_a_minimal_env(self):
        claude = FakeClaude()
        make({'resolver.haiku_max_budget_usd': 0.02}, claude=claude).classify('texto da tarefa')
        argv, prompt, env, _ = claude.calls[0]
        self.assertEqual(argv[:2], ['claude', '-p'])
        for flag, value in (('--model', 'haiku'), ('--effort', 'low'), ('--output-format', 'json'),
                            ('--max-budget-usd', '0.02')):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn('--no-session-persistence', argv)
        self.assertEqual(json.loads(argv[argv.index('--json-schema') + 1])['required'], ['choice'])
        self.assertTrue(prompt.endswith('texto da tarefa'))
        self.assertNotIn('CLAUDE_CONFIG_DIR', env)
        self.assertLessEqual(set(env), {'HOME', 'PATH', 'LANG'})


class M11CasesTest(unittest.TestCase):
    def test_the_fixture_has_the_ten_layer_and_the_five_account_cases(self):
        self.assertEqual((len(CASES['layers']), len(CASES['accounts'])), (10, 5))
        self.assertEqual({c['layer'] for c in CASES['layers']}, {'alta', 'media', 'rapida'})

    def test_the_layer_cases_through_the_tev1_double_and_through_the_haiku_double(self):
        for threshold, path in ((0.5, 'tev1'), (None, 'haiku')):
            r = make({'resolver.tev1_min_confidence': threshold})
            for case in CASES['layers']:
                out = r.classify(case['text'])
                self.assertEqual((out['layer'], out['path']), (case['layer'], path), case['id'])

    def test_the_account_cases_by_the_table_in_both_orders(self):
        for case in CASES['accounts']:
            for order in (list(case['candidates']), list(reversed(case['candidates']))):
                ordered = {name: case['candidates'][name] for name in order}
                out = res.choose_account(ordered, CASES['hard_cap'], CASES['margin'])
                self.assertEqual(out['account'], case['account'], f'{case["id"]} {order}')


if __name__ == '__main__':
    unittest.main()
