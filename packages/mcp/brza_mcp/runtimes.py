"""A tabela de runtimes do MCP (PLN-013/D1)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Runtime:
    name: str             # o do `dispatch` e da coluna `runtime` da fila
    agent: str            # o `--agent` do `worker-start`
    efforts: tuple        # teto largo do runtime; a conferência fina é por modelo (MODEL_EFFORTS, REV-010/A6)
    accounts: str | None = None   # E2: a chave de topo do `account list` com as contas; None = conta única
    inactive: str | None = None   # E2: a chave em `rateLimits` com o uso das contas inativas
    switch: str | None = None     # E2: 'chat' (agentSession.switchAccount, H3) ou 'global' (accounts.selectCodex)


RUNTIMES = {r.name: r for r in (
    Runtime('claude', 'claude', ('low', 'medium', 'high', 'xhigh', 'max'),
            'claude', 'inactiveClaudeAccounts', 'chat'),
    Runtime('codex', 'codex', ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
            'codex', 'inactiveCodexAccounts', 'global'),
    Runtime('grok', 'grok', ('low', 'medium', 'high', 'xhigh')),
    Runtime('antigravity', 'antigravity', ('low', 'medium', 'high')),
)}
ALIASES = {'agy': 'antigravity'}   # R4.4: normalizado antes da fila

# a escada por modelo (REV-010/A6). Fontes: o catálogo do app
# (brza-studio/src/shared/agent-session/options/agent-session-option-catalog-grok.ts:10-15,56-75) e os caches dos
# runtimes (~/.grok/models_cache.json; ~/.codex/models_cache.json). Modelo fora da tabela: vale o teto do runtime, e o
# `worker-start` é quem recusa (brza-studio/src/shared/agent-launch-model-preferences.ts:64-77).
MODEL_EFFORTS = {
    ('grok', 'grok-4.7'): ('low', 'medium', 'high', 'xhigh'),
    ('grok', 'grok-4.7-build-fast'): ('low', 'medium', 'high', 'xhigh'),
    ('grok', 'grok-4.6'): ('low', 'medium', 'high', 'xhigh'),
    ('grok', 'grok-4.5'): ('low', 'medium', 'high'),
    ('codex', 'gpt-6.1-sol'): ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    ('codex', 'gpt-6-astra'): ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    ('codex', 'gpt-6-luna'): ('low', 'medium', 'high', 'xhigh', 'max'),
}


def get(name: str | None) -> Runtime:
    """O runtime pelo nome ou alias; None é o Claude. Desconhecido levanta KeyError."""
    name = name or 'claude'
    return RUNTIMES[ALIASES.get(name, name)]


def efforts(runtime: str, model: str | None) -> tuple:
    return MODEL_EFFORTS.get((runtime, model), get(runtime).efforts)
