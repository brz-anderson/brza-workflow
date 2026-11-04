"""O registro de ferramentas do MCP: quem acrescenta uma ferramenta chama `register`, sem mexer no servidor.

    from brza_mcp.registry import REGISTRY
    REGISTRY.register('status', 'Estado do fluxo', handler, {'type': 'object', 'properties': {}})

O handler recebe (argumentos, contexto) e devolve texto, um dict (vira JSON em texto) ou o resultado MCP inteiro
(um dict com `content`). A exceção vira resultado com `isError`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class CallContext:
    """O que o handler sabe da requisição: a sessão (X-Brza-Session) e os cabeçalhos."""
    session: str | None = None
    headers: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    handler: object
    input_schema: dict


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, name, description, handler, input_schema=None):
        self._tools[name] = Tool(name, description, handler, input_schema or {'type': 'object', 'properties': {}})

    def tool(self, name, description, input_schema=None):
        """Decorador de `register`."""
        def wrap(handler):
            self.register(name, description, handler, input_schema)
            return handler
        return wrap

    def get(self, name):
        return self._tools.get(name)

    def list(self) -> list[dict]:
        return [{'name': t.name, 'description': t.description, 'inputSchema': t.input_schema}
                for t in self._tools.values()]

    def call(self, name, arguments, ctx: CallContext) -> dict:
        """O resultado do `tools/call`; levanta KeyError se a ferramenta não existe."""
        tool = self._tools[name]
        try:
            out = tool.handler(arguments, ctx)
        except Exception as e:  # noqa: BLE001 - o handler erra à vontade; o servidor não cai
            return {'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}], 'isError': True}
        if isinstance(out, dict) and 'content' in out:
            return {'isError': False, **out}
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return {'content': [{'type': 'text', 'text': text}], 'isError': False}


REGISTRY = ToolRegistry()
