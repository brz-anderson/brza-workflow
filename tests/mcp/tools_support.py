"""Apoio dos testes das ferramentas: o servidor de verdade com `dispatch`, `status` e `config` instalados sobre uma
fila em memória e um leitor de índice trocado, e o `tools/call` em HTTP."""
import json

from mcp_support import ServerCase

from brza_mcp import limits as lim, queue as q, tools  # noqa: E402

INDEX = '\n'.join(json.dumps(e) for e in (
    {'id': 'PLN-9', 'priority': 'low', 'blocks': 'PLN-3', 'deadline': '2026-12-01'},
    {'id': 'ISS-1', 'priority': 'high'},
))


class ToolsCase(ServerCase):
    machine = staticmethod(lambda: {})
    config = None

    def setUp(self):
        super().setUp()
        self.queue = q.Queue(':memory:')
        self.addCleanup(self.queue.close)
        self.limits = lim.Limits(self.config, machine=self.machine)
        self.service = tools.install(self.registry, queue=self.queue, limits=self.limits, workspace='/ws',
                                     index_reader=lambda workspace: INDEX)

    def call(self, name, arguments=None, session=None):
        """(isError, conteúdo): o JSON do texto quando decodifica, senão o texto."""
        headers = {'X-Brza-Session': session} if session else None
        status, _, msg = self.rpc('tools/call', {'name': name, 'arguments': arguments or {}}, headers=headers)
        self.assertEqual(status, 200)
        result = msg['result']
        text = result['content'][0]['text']
        try:
            return result['isError'], json.loads(text)
        except ValueError:
            return result['isError'], text
