"""O `CloudRuntime` no degrau 1: `list` da run de `CURSOR_CONVERSATION_ID` e `read` com transcript ausente.

`usage`, `open` e `send` ficam de fora até serem provados. `open` entra depois que `agent -p` sair 0, sem
`--force`. O argv desse comando já está aqui, para o dry-run imprimir, e ninguém o executa neste degrau.
"""

import os

from agent_runtime import ABSENT, SessionRecord, absent_metric, require_capability
from brza_runtime import BrzaError


class CloudRuntime:
    name = 'cloud'

    def __init__(self, env=None):
        self.env = os.environ if env is None else env

    def capabilities(self):
        return frozenset({'list', 'read'})

    def list(self):
        require_capability(self, 'list')
        cid = (self.env.get('CURSOR_CONVERSATION_ID') or '').strip()
        if not cid:
            raise BrzaError('cloud: CURSOR_CONVERSATION_ID ausente; nada foi listado')
        return [SessionRecord(id=cid, source='cloud', agent='cursor', title=None, status='running',
                              started_at=None, metrics={'cost_usd': absent_metric(), 'tokens': absent_metric()})]

    def read(self, session_id=None):
        """O transcript ainda não está na máquina. O campo sai `ausente`, e nada é lido do Studio."""
        require_capability(self, 'read')
        cid = (self.env.get('CURSOR_CONVERSATION_ID') or '').strip()
        return {'id': session_id or cid or None, 'source': 'cloud', 'transcript': ABSENT}

    def usage(self, session_id):
        require_capability(self, 'usage')
        raise BrzaError('cloud: usage sem implementação')

    def open(self, spec):
        require_capability(self, 'open')
        raise BrzaError('cloud: open sem implementação')

    def send(self, session_id, message):
        require_capability(self, 'send')
        raise BrzaError('cloud: send sem implementação')


def open_argv(model=None, workspace=None, chat_id=None):
    """O argv do `open` provado no P7. Sem `--force`. Placeholders enquanto o dry-run não tem os valores."""
    return ['agent', '-p', '--output-format', 'json', '--model', model or '<id>',
            '--workspace', str(workspace) if workspace else '<dir>', '--trust',
            '--resume', chat_id or '<chatId>']
