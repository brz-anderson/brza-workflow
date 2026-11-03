"""O desempatador (D4, M11): a tabela decide o determinístico, o `tev1:4b` local desempata o texto e o Haiku fica
abaixo do limiar de confiança.

    resolver = Resolver(config=policy.config)
    resolver.classify('Refatorar o módulo de filas')
    # {'layer': 'alta', 'path': 'tev1', 'confidence': 0.51, 'ms': 94, 'reason': '...', 'tev1_confidence': 0.51}

`path` é `tev1`, `haiku` ou `fallback` (o `table` é o de quem não precisou do texto, que `choose_account` e a política
devolvem). Sem `resolver.tev1_min_confidence` tudo vai ao Haiku (R2.2). Sem Ollama ou com erro dele, vai direto ao
Haiku; sem `claude` ou com erro dele, `layer` é None e a política fica na linha do `consultant`. O Ollama e o `claude`
são injetáveis (`ollama(url, body, timeout) -> dict`, `claude(argv, input, env, timeout) -> (returncode, stdout)`);
nenhum dos dois levanta para quem chama. O desempate não conta no teto de workers (R2.3): não passa pela fila.

O `/v1/systemone` do Ollama 0.40 (medido na máquina): `{"model", "keep_alive", "state": <texto>, "questions":
{<nome>: {"type": "choice", "instructions": <texto>, "criteria": {<opção>: <descrição>}}}}`, e a resposta traz
`answers.<nome> = {"choice", "probabilities", "confidence"}`.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.request

LAYERS = {'alta': 'desenho, decisão, julgamento entre alternativas, revisão adversária',
          'media': 'implementação, leitura, coleta, busca, extração e verificação mecânica de volume',
          'rapida': 'tarefa curta e mecânica: listar, contar, formatar, renomear'}
QUESTION = 'layer'
INSTRUCTIONS = ('Escolha a camada de modelo que a tarefa em state pede: alta para desenho, decisão e revisão; '
                'media para implementação e coleta; rapida para tarefa curta e mecânica.')
LAYER_SCHEMA = {'type': 'object', 'properties': {'choice': {'enum': list(LAYERS)}}, 'required': ['choice'],
                'additionalProperties': False}
HAIKU_PROMPT = ('Escolha a camada de modelo que a tarefa abaixo pede, em "choice": '
                + '; '.join(f'{k} = {v}' for k, v in LAYERS.items()) + '.\n\nTarefa:\n')
OLLAMA_TIMEOUT = 20.0
CLAUDE_TIMEOUT = 60.0


def post_ollama(url, body, timeout):
    """O POST ao Ollama, sem proxy; levanta OSError ou ValueError, que o `Resolver` trata."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
        return json.load(r)


def run_claude(argv, input, env, timeout):  # noqa: A002
    """O `claude` real (custa dinheiro: os testes trocam esta função); FileNotFoundError quando não está no PATH."""
    # fora do workspace: o `claude -p` carrega o CLAUDE.md e as regras do cwd, e a classificação não precisa delas
    done = subprocess.run(argv, input=input, capture_output=True, text=True, env=env, timeout=timeout,
                          cwd=tempfile.gettempdir())
    return done.returncode, done.stdout


def claude_env():
    """O ambiente mínimo do H2: a conta `systemDefault` vai sem `CLAUDE_CONFIG_DIR`."""
    from .accounts import claude_env as account_env
    return account_env(None, {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG') if k in os.environ})


class Resolver:
    def __init__(self, config=None, ollama=post_ollama, claude=run_claude, clock=time.monotonic):
        self.config = config or (lambda: {})
        self.ollama, self.claude, self.clock = ollama, claude, clock

    def _get(self, key, default=None):
        value = self.config().get(key)
        return default if value is None else value

    def _limit(self):
        """O limiar do `tev1`, em número; texto que não é número (o `brza.toml` aceita qualquer tipo) vale como ausente."""
        value = self._get('resolver.tev1_min_confidence')
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def classify(self, text) -> dict:
        start = self.clock()
        tev1_conf, reason = None, None
        tev1 = self._tev1(text)
        if tev1 is not None:
            tev1_conf = tev1['confidence']
            limit = self._limit()
            if limit is None:
                reason = 'sem resolver.tev1_min_confidence: tudo vai ao Haiku'
            elif tev1_conf < limit:
                reason = f'confiança {tev1_conf:.2f} abaixo de {limit}'
            else:
                return self._answer(start, tev1['choice'], 'tev1', tev1_conf, 'tev1', tev1_conf)
        else:
            reason = 'sem resposta do tev1'
        haiku = self._haiku(text)
        if haiku is not None:
            return self._answer(start, haiku, 'haiku', None, f'{reason}; Haiku', tev1_conf)
        return self._answer(start, None, 'fallback', None, f'{reason}; sem resposta do Haiku: fica a tabela', tev1_conf)

    def _answer(self, start, layer, path, confidence, reason, tev1_conf):
        return {'layer': layer, 'path': path, 'confidence': confidence, 'ms': round((self.clock() - start) * 1000),
                'reason': reason, 'tev1_confidence': tev1_conf}

    def _tev1(self, text):
        url = self._get('resolver.ollama_url', 'http://127.0.0.1:11434').rstrip('/') + '/v1/systemone'
        body = {'model': self._get('resolver.tev1_model', 'tev1:4b'),
                'keep_alive': self._get('resolver.tev1_keep_alive', '1m'),
                'state': text,
                'questions': {QUESTION: {'type': 'choice', 'instructions': INSTRUCTIONS, 'criteria': LAYERS}}}
        try:
            answer = self.ollama(url, body, OLLAMA_TIMEOUT)['answers'][QUESTION]
            choice, confidence = answer['choice'], float(answer['confidence'])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return {'choice': choice, 'confidence': confidence} if choice in LAYERS else None

    def _haiku(self, text):
        argv = ['claude', '-p', '--model', 'haiku', '--effort', 'low', '--output-format', 'json',
                '--json-schema', json.dumps(LAYER_SCHEMA), '--max-budget-usd',
                str(self._get('resolver.haiku_max_budget_usd', 0.01)), '--no-session-persistence']
        try:
            code, out = self.claude(argv, HAIKU_PROMPT + text, claude_env(), CLAUDE_TIMEOUT)
            if code != 0:
                return None
            choice = json.loads(out)['structured_output']['choice']
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return None
        return choice if choice in LAYERS else None


def choose_account(candidates, hard_cap=90.0, margin=5.0, soft_cap=None) -> dict:
    """A cota é da tabela, não do modelo (M11): `candidates` = `{conta: [usedPercent, ...]}` das janelas (5 h, semanal).

    Fora quem tem alguma janela no teto ou acima; entre os demais, a de menor janela mais cheia; dentro da margem de
    `margin` pontos do melhor, empata e a ordem alfabética decide, para a resposta não mudar com a ordem da entrada.
    Todas no teto: `account` None. Conta sem janela fica de fora (sem leitura, a cota é do teto de sessões). Com
    `soft_cap`, as contas abaixo dele passam à frente: só se nenhuma está abaixo é que as acima dele concorrem.
    Os valores são o maior entre o uso e a projeção de cada janela (`Limits.pick_account`).
    """
    usage = {name: max(windows) for name, windows in candidates.items() if windows}
    open_ = {n: u for n, u in usage.items() if u < hard_cap}
    if not open_:
        return {'account': None, 'reason': f'todas as contas no teto de {hard_cap:g}%'}
    if soft_cap is not None:
        open_ = {n: u for n, u in open_.items() if u < soft_cap} or open_
    best = min(open_.values())
    tied = sorted(n for n, u in open_.items() if u - best <= margin)
    return {'account': tied[0], 'reason': f'menor uso {best:g}%' + (f', empate na margem de {margin:g} pontos'
                                                                    if len(tied) > 1 else '')}
