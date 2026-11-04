"""A política declarativa (D4): o `brza.toml` do workspace, as sobreposições do `config` e a decisão de modelo.

    policy = Policy(workspace, overrides=service.get_overrides)
    policy.config()                          # a configuração efetiva, chaves planas do `brza.toml`
    policy.decide_model({'role': 'executor'})  # {'model': 'sonnet', 'effort': 'medium', 'reason': 'policy', ...}

A precedência é: os padrões do D4, o `<BRZA_WORKSPACE>/brza.toml` e, por cima, as sobreposições gravadas pelo `config`
(M10). O TOML é lido por `tomllib` (3.11+); abaixo disso, ou com `use_parse_toml=True`, pelo `parse_toml` do `brza_root`
do `brza-workflow`. O `parse_toml` recusa float, e o `haiku_max_budget_usd` é um: o caminho dele guarda o literal
decimal como texto marcado e o devolve como float.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
from pathlib import Path

from . import runtimes

ROLES = ('consultant', 'executor', 'architect')
LAYER_ROLE = {'alta': 'architect', 'media': 'executor', 'rapida': 'consultant'}  # a camada do AGENTS.md -> a linha

DEFAULTS = {
    'mcp.port': 4242,
    'orchestrator.max_workers': 6,
    'policy.enforce': True,
    'policy.suggest_to_main': False,  # até haver sinal do que a sessão principal faz (R6.4)
    'policy.roles.consultant.claude.model': 'sonnet', 'policy.roles.consultant.claude.effort': 'low',
    'policy.roles.executor.claude.model': 'sonnet', 'policy.roles.executor.claude.effort': 'medium',
    'policy.roles.architect.claude.model': 'opus', 'policy.roles.architect.claude.effort': 'high',
    'policy.roles.consultant.codex.model': 'gpt-6.1-sol', 'policy.roles.consultant.codex.effort': 'low',
    'policy.roles.executor.codex.model': 'gpt-6.1-sol', 'policy.roles.executor.codex.effort': 'medium',
    'policy.roles.architect.codex.model': 'gpt-6-astra', 'policy.roles.architect.codex.effort': 'high',
    'policy.roles.consultant.grok.model': 'grok-4.7', 'policy.roles.consultant.grok.effort': 'low',
    'policy.roles.executor.grok.model': 'grok-4.7', 'policy.roles.executor.grok.effort': 'high',
    'policy.roles.architect.grok.model': 'grok-4.7', 'policy.roles.architect.grok.effort': 'xhigh',
    'policy.roles.consultant.antigravity.model': 'gemini-4-argon-low',
    'policy.roles.executor.antigravity.model': 'gemini-4-argon-medium',
    'policy.roles.architect.antigravity.model': 'gemini-4-argon-high',
    'quota.soft_cap_percent': 55,
    'quota.hard_cap_percent': 90,
    'quota.max_sessions_per_account': 2,
    'resolver.ollama_url': 'http://127.0.0.1:11434',
    'resolver.tev1_model': 'tev1:4b',
    'resolver.tev1_keep_alive': '1m',
    'resolver.haiku_max_budget_usd': 0.01,
}  # `machine.*` e `resolver.tev1_min_confidence` ficam sem valor de propósito (R2.2)

# as chaves de sobreposição são as do `brza.toml` (D4), em lista fechada: chave com erro de digitação se recusa em vez
# de gravar um valor que nada lê. Cada uma leva o tipo e a faixa: (tipo, mínimo, máximo, mínimo exclusivo)
KEYS = {
    'policy.enforce': ('bool',),
    'policy.suggest_to_main': ('bool',),
    'orchestrator.max_workers': ('int', 0, None, False),
    'quota.soft_cap_percent': ('number', 0, 100, True),
    'quota.hard_cap_percent': ('number', 0, 100, True),
    'quota.max_sessions_per_account': ('int', 0, None, False),
    'machine.min_free_mem_gb': ('number', 0, None, False),
    'machine.max_swap_gb': ('number', 0, None, False),
    'machine.max_cpu_percent': ('number', 0, 100, True),
    'resolver.ollama_url': ('string',),
    'resolver.tev1_model': ('string',),
    'resolver.tev1_keep_alive': ('string',),
    'resolver.tev1_min_confidence': ('number', 0, 1, False),
    'resolver.haiku_max_budget_usd': ('number', 0, None, True),
}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_value(key, value):
    """Confere o valor de uma chave de `KEYS` pelo tipo e pela faixa; levanta `ValueError` com o motivo."""
    kind, *bounds = KEYS[key]
    shown = json.dumps(value)
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f'{key} é booleano, veio {shown}')
    elif kind == 'string':
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} é texto não vazio, veio {shown}')
    else:
        if not _is_number(value) or not math.isfinite(value) or (kind == 'int' and not isinstance(value, int)):
            raise ValueError(f'{key} é {"inteiro" if kind == "int" else "numérico finito"}, veio {shown}')
        low, high, low_open = bounds
        if value < low or (low_open and value == low) or (high is not None and value > high):
            span = f'{"maior que" if low_open else "a partir de"} {low}' + (f' e até {high}' if high is not None else '')
            raise ValueError(f'{key} fica {span}, veio {shown}')


NAMESPACES = ('mcp', 'orchestrator', 'policy', 'quota', 'machine', 'resolver')  # o resto do brza.toml não é daqui
_ROLE_KEY = re.compile(rf'^policy\.roles\.[A-Za-z0-9_-]+\.({"|".join(runtimes.RUNTIMES)})\.(model|effort)$')
_FLAT_ROLE_KEY = re.compile(r'^policy\.roles\.([A-Za-z0-9_-]+)\.(model|effort)$')


def check_file_key(key, value):
    """Confere uma chave plana do `brza.toml` com a mesma tabela da ferramenta `config`, mais `mcp.port` e as linhas
    `policy.roles.<papel>.<runtime>.{model,effort}`; chave desconhecida ou tipo errado levanta `ValueError`."""
    if key.split('.')[0] not in NAMESPACES:
        return
    if key in KEYS:
        check_value(key, value)
    elif key == 'mcp.port':
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(f'mcp.port é um inteiro de 1 a 65535, veio {json.dumps(value)}')
    elif m := _ROLE_KEY.match(key):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} é texto não vazio, veio {json.dumps(value)}')
        runtime, field = m.group(1), m.group(2)
        if field == 'effort':
            runtime_obj = runtimes.get(runtime)
            if value not in runtime_obj.efforts:
                raise ValueError(f'{key}: esforço {value!r} fora do teto do runtime {runtime} '
                                 f'({", ".join(runtime_obj.efforts)})')
    elif m := _FLAT_ROLE_KEY.match(key):
        raise ValueError(f'{key} virou policy.roles.{m[1]}.claude.{m[2]} (PLN-013/R3.1)')
    else:
        raise ValueError(f'chave desconhecida: {key!r} (confira o nome)')


def check_role_rows(values):
    """Depois do flatten: o esforço de cada linha contra a escada do modelo dela, `runtimes.efforts(runtime, model)`
    (REV-010/A6). Fora da escada levanta ValueError com a linha, o modelo e a escada."""
    pairs = set()
    for key in values:
        m = re.match(rf'^policy\.roles\.([A-Za-z0-9_-]+)\.({"|".join(runtimes.RUNTIMES)})\.', key)
        if m:
            pairs.add((m.group(1), m.group(2)))
    for role, runtime in sorted(pairs):
        effort = values.get(f'policy.roles.{role}.{runtime}.effort')
        if effort is not None:
            model = values.get(f'policy.roles.{role}.{runtime}.model')
            allowed = runtimes.efforts(runtime, model)
            if effort not in allowed:
                raise ValueError(f'policy.roles.{role}.{runtime}: esforço {effort!r} fora da escada do modelo '
                                 f'{model!r} ({", ".join(allowed)})')


_FLOAT_LINE = re.compile(r'^(\s*[A-Za-z0-9_-]+\s*=\s*)(-?\d+\.\d+)(\s*(?:#.*)?)$')
_MARK = '@float:'


def _brza_root():
    path = Path(__file__).resolve().parents[3] / 'core' / '.claude' / 'hooks' / 'brza_root.py'
    spec = importlib.util.spec_from_file_location('brza_root_for_policy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unmark(node):
    if isinstance(node, dict):
        return {k: _unmark(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_unmark(v) for v in node]
    if isinstance(node, str) and node.startswith(_MARK):
        return float(node[len(_MARK):])
    return node


def parse_text(text, use_parse_toml=False) -> dict:
    """O dict do TOML: `tomllib` quando existe, senão (ou forçado) o `parse_toml`, com o float contornado."""
    if not use_parse_toml:
        try:
            import tomllib
        except ImportError:
            pass
        else:
            return tomllib.loads(text)
    lines = [_FLOAT_LINE.sub(lambda m: f'{m.group(1)}"{_MARK}{m.group(2)}"{m.group(3)}', line)
             for line in text.splitlines()]
    return _unmark(_brza_root().parse_toml('\n'.join(lines) + '\n'))


def flatten(node, prefix='') -> dict:
    out = {}
    for key, value in node.items():
        if isinstance(value, dict):
            out.update(flatten(value, f'{prefix}{key}.'))
        else:
            out[f'{prefix}{key}'] = value
    return out


class PolicyError(Exception):
    """O `brza.toml` existe e não lê: o servidor não adivinha, o erro diz o arquivo."""


class Policy:
    def __init__(self, workspace=None, overrides=None, use_parse_toml=False):
        self.workspace = workspace
        self.overrides = overrides or (lambda: {})
        self.use_parse_toml = use_parse_toml

    def path(self):
        return Path(self.workspace) / 'brza.toml' if self.workspace else None

    def load(self) -> dict:
        """Os padrões do D4 por baixo do `brza.toml`, chaves planas; arquivo ausente deixa os padrões."""
        path = self.path()
        values = dict(DEFAULTS)
        if path is None or not path.is_file():
            return values
        try:
            found = flatten(parse_text(path.read_text(), self.use_parse_toml))
            for key, value in found.items():  # a chave com erro de digitação some sem erro, e o tipo errado estoura depois
                check_file_key(key, value)
            merged = {**DEFAULTS, **found}
            check_role_rows(merged)
        except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError e brza_root.TomlError são ValueError
            raise PolicyError(f'{path}: {e}') from e
        values.update(found)
        return values

    def config(self) -> dict:
        """A configuração efetiva: o TOML (ou os padrões) e, por cima, as sobreposições."""
        return {**self.load(), **self.overrides()}

    # ---- o modelo ----

    def role_row(self, role, runtime='claude', config=None) -> dict | None:
        """A linha do papel NO runtime; nunca a de outro runtime."""
        config = self.config() if config is None else config
        row = {f: config[f'policy.roles.{role}.{runtime}.{f}'] for f in ('model', 'effort')
               if config.get(f'policy.roles.{role}.{runtime}.{f}') is not None}
        return row or None

    def decide_model(self, request, resolver=None) -> dict:
        """`{model, effort, reason, role, resolver}` do pedido; `reason` diz por que cada valor ficou.

        `policy.enforce = false` devolve o pedido como veio (`passthrough`). Com a imposição, o papel dá o modelo e o
        esforço, e o que o pedido trouxe vale só onde o papel não define. Papel sem linha vai ao `resolver` (o
        desempatador do M11) pelo texto da tarefa; sem `resolver`, ou sem resposta dele, fica a linha do `consultant`.
        """
        config = self.config()
        role, asked = request.get('role'), {k: request.get(k) for k in ('model', 'effort')}
        if not config.get('policy.enforce', True):
            return {**asked, 'reason': 'passthrough', 'role': role, 'resolver': None}
        runtime = runtimes.get(request.get('runtime')).name
        row = self.role_row(role, runtime, config)
        resolved, reason = None, 'policy'
        if row is None and runtime == 'claude':
            row = self.role_row('consultant', 'claude', config) or {}
            reason = 'default_role'
            if resolver is not None and request.get('task'):
                resolved = resolver.classify(request['task'])
                layer_row = self.role_row(LAYER_ROLE.get(resolved.get('layer')), 'claude', config)
                if layer_row:
                    row, reason = layer_row, f'resolver:{resolved["path"]}'
        elif row is None:
            # fora do Claude (REV-010/A7): sem resolver, sem a linha de outro papel; o pedido vale onde trouxe
            # model/effort, e o resto fica None, com reason 'default_role; padrão do runtime'
            row = {}
            reason = 'default_role; padrão do runtime'
        out, notes = {}, []
        for field in ('model', 'effort'):
            if row.get(field) is not None:
                out[field] = row[field]
            elif asked[field] is not None:
                out[field] = asked[field]
                notes.append(f'{field} do pedido, o papel não o define')
            else:
                out[field] = None
        if notes:
            reason = f'{reason}; ' + '; '.join(notes)
        if out.get('effort') is not None and out.get('model') is None:
            out['effort'] = None
            reason = f'{reason}; o worker-start exige --model com --effort'
        return {**out, 'reason': reason, 'role': role, 'resolver': resolved}

    def suggestion(self) -> dict | None:
        """A sugestão de modelo para a sessão principal (R6.4), por aviso; None com a chave desligada."""
        config = self.config()
        if not config.get('policy.enforce', True) or not config.get('policy.suggest_to_main', False):
            return None
        row = self.role_row('architect', 'claude', config)
        if row is None:
            return None
        return {**row, 'role': 'architect',
                'notice': f'a política sugere {row.get("model")} ({row.get("effort")}) para a sessão principal'}
