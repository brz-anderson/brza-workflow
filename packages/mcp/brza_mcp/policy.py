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
import re
from pathlib import Path

ROLES = ('consultant', 'executor', 'architect')
LAYER_ROLE = {'alta': 'architect', 'media': 'executor', 'rapida': 'consultant'}  # a camada do AGENTS.md -> a linha

DEFAULTS = {
    'mcp.port': 4242,
    'orchestrator.max_workers': 6,
    'policy.enforce': True,
    'policy.suggest_to_main': False,  # até haver sinal do que a sessão principal faz (R6.4)
    'policy.roles.consultant.model': 'sonnet', 'policy.roles.consultant.effort': 'low',
    'policy.roles.executor.model': 'sonnet', 'policy.roles.executor.effort': 'medium',
    'policy.roles.architect.model': 'opus', 'policy.roles.architect.effort': 'high',
    'quota.soft_cap_percent': 55,
    'quota.hard_cap_percent': 90,
    'quota.max_sessions_per_account': 2,
    'resolver.ollama_url': 'http://127.0.0.1:11434',
    'resolver.tev1_model': 'tev1:4b',
    'resolver.tev1_keep_alive': '1m',
    'resolver.haiku_max_budget_usd': 0.01,
}  # `machine.*` e `resolver.tev1_min_confidence` ficam sem valor de propósito (R2.2)

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
            values.update(flatten(parse_text(path.read_text(), self.use_parse_toml)))
        except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError e brza_root.TomlError são ValueError
            raise PolicyError(f'{path}: {e}') from e
        return values

    def config(self) -> dict:
        """A configuração efetiva: o TOML (ou os padrões) e, por cima, as sobreposições."""
        return {**self.load(), **self.overrides()}

    # ---- o modelo ----

    def role_row(self, role, config=None) -> dict | None:
        config = self.config() if config is None else config
        row = {f: config[f'policy.roles.{role}.{f}'] for f in ('model', 'effort')
               if config.get(f'policy.roles.{role}.{f}') is not None}
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
        row, resolved, reason = self.role_row(role, config), None, 'policy'
        if row is None:
            row = self.role_row('consultant', config) or {}
            reason = 'default_role'
            if resolver is not None and request.get('task'):
                resolved = resolver.classify(request['task'])
                layer_row = self.role_row(LAYER_ROLE.get(resolved.get('layer')), config)
                if layer_row:
                    row, reason = layer_row, f'resolver:{resolved["path"]}'
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
        return {**out, 'reason': reason, 'role': role, 'resolver': resolved}

    def suggestion(self) -> dict | None:
        """A sugestão de modelo para a sessão principal (R6.4), por aviso; None com a chave desligada."""
        config = self.config()
        if not config.get('policy.enforce', True) or not config.get('policy.suggest_to_main', False):
            return None
        row = self.role_row('architect', config)
        if row is None:
            return None
        return {**row, 'role': 'architect',
                'notice': f'a política sugere {row.get("model")} ({row.get("effort")}) para a sessão principal'}
